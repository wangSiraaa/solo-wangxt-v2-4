"""排程核心与服务的确定性测试（使用独立的测试数据库）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import astronomy, scheduler
from app.config import DATABASE_URL
from app.models import (
    AcquiredFrame,
    Night,
    Override,
    PlanBlock,
    Proposal,
    Site,
    Target,
    WeatherInterval,
)
from app import service

XINGLONG = astronomy.SiteInfo(40.3959, 117.5786, 900.0)


# ---------- 纯天文 / 调度 ----------
class T:
    def __init__(self, tid, ra, dec, prio=2, exp=600, frames=2, filt="V",
                 alt=30.0, moon=40.0):
        self.id = tid
        self.ra_deg, self.dec_deg = ra, dec
        self.priority = prio
        self.exposure_seconds = exp
        self.readout_seconds = 60
        self.requested_frames = frames
        self.filter_name = filt
        self.min_altitude_deg = alt
        self.min_moon_separation_deg = moon


def test_dark_window_september():
    s, e = astronomy.night_window(XINGLONG, "2026-09-18")
    dark = astronomy.sun_below_intervals(XINGLONG, s, e)
    assert len(dark) == 1
    a, b = dark[0]
    # 北京 19:48–04:20 左右（UTC 11:48–20:20）
    assert abs((a - datetime(2026, 9, 18, 11, 48, tzinfo=timezone.utc)).total_seconds()) < 600
    assert abs((b - datetime(2026, 9, 18, 20, 20, tzinfo=timezone.utc)).total_seconds()) < 600


def test_infeasible_targets_reasons():
    s, e = astronomy.night_window(XINGLONG, "2026-09-18")
    dark = astronomy.sun_below_intervals(XINGLONG, s, e)
    omega_cen = T(1, 201.697, -47.4795)
    f = astronomy.assess_target(XINGLONG, omega_cen, s, e, dark)
    assert not f.feasible
    assert any("高度角" in r for r in f.reasons)
    # 靠月亮的目标（月 RA≈261.6, Dec≈-28.5）
    near_moon = T(2, 263.0, -12.0)
    f2 = astronomy.assess_target(XINGLONG, near_moon, s, e, dark)
    assert not f2.feasible
    assert any("月距" in r for r in f2.reasons)


def _ctx(tmp_start, length_hours=8, weather=None, targets=None, acquired=None,
         as_of=None, masks_all=True, carried=None, pins=None, holds=None,
         sky_reasons=None):
    end = tmp_start + timedelta(hours=length_hours)
    dark = [(tmp_start, end)]
    targets = targets or [T(1, 10.0, 40.0, frames=3, exp=600)]
    times = astronomy.grid_times(tmp_start, end)
    masks = {
        t.id: scheduler._interval_mask(times, dark) if masks_all else None
        for t in targets
    }
    return scheduler.PlanContext(
        start=tmp_start,
        end=end,
        dark_intervals=dark,
        weather_intervals=weather or [],
        targets=targets,
        feas_masks=masks,
        sky_reasons=sky_reasons or {},
        acquired=acquired or {},
        as_of=as_of or tmp_start,
        carried=carried or [],
        pins=pins or [],
        holds=holds or [],
    )


def test_setup_and_filter_change_consume_time():
    t0 = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    ctx = _ctx(t0, targets=[T(1, 10, 40, exp=600, frames=1)])
    r = scheduler.build_plan(ctx)
    kinds = [b.kind for b in r.blocks]
    assert kinds == ["setup", "filter_change", "science"]
    # 准备 10 分钟、切换 3 分钟、曝光含读出向上取整到 2 分钟网格
    setup, change, sci = r.blocks
    assert (setup.end - setup.start).total_seconds() == 600      # 10 分钟准备
    assert (change.end - change.start).total_seconds() == 240   # 3 分钟取整到网格
    assert sci.start >= change.end >= setup.end


def test_filter_change_only_when_filter_differs():
    t0 = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    targets = [
        T(1, 10, 40, prio=1, frames=1, filt="V"),
        T(2, 20, 50, prio=2, frames=1, filt="V"),  # 同滤镜
        T(3, 30, 60, prio=3, frames=1, filt="B"),
    ]
    r = scheduler.build_plan(_ctx(t0, targets=targets))
    changes = [b for b in r.blocks if b.kind == "filter_change"]
    # 初始 V（1 次）+ V->B（1 次）= 2 次，而不是每个目标都切
    assert len(changes) == 2


def test_weather_shrinks_window_and_replan_keeps_acquired():
    t0 = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    tgt = T(1, 10, 40, frames=3, exp=600)
    # v1：全夜 3 帧
    v1 = scheduler.build_plan(_ctx(t0, targets=[tgt]))
    assert len([b for b in v1.blocks if b.kind == "science"]) == 3
    # 模拟 12:20 前完成第 1 帧；12:20–13:00 天气
    # 重排：acquired=1，as_of=13:00，应只再排 2 帧
    ctx = _ctx(
        t0,
        targets=[tgt],
        acquired={1: 1},
        as_of=datetime(2026, 9, 18, 13, 0, tzinfo=timezone.utc),
        weather=[
            (
                datetime(2026, 9, 18, 12, 20, tzinfo=timezone.utc),
                datetime(2026, 9, 18, 13, 0, tzinfo=timezone.utc),
            )
        ],
    )
    v2 = scheduler.build_plan(ctx)
    sci = [b for b in v2.blocks if b.kind == "science"]
    assert [b.frame_seq for b in sci] == [2, 3]
    assert all(b.start >= datetime(2026, 9, 18, 13, 0, tzinfo=timezone.utc) for b in sci)


def test_insufficient_window_unsaturates_target():
    # 只有 40 分钟黑夜，但两目标各需大量帧
    t0 = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
    targets = [T(1, 10, 40, frames=10, exp=600), T(2, 20, 50, frames=10, exp=600)]
    ctx = _ctx(t0, length_hours=1, targets=targets)
    r = scheduler.build_plan(ctx)
    total_sci = len([b for b in r.blocks if b.kind == "science"])
    assert total_sci < 20
    assert 1 in r.summary["targets_unsaturated"] or 2 in r.summary["targets_unsaturated"]
    # 解释里给出"剩余窗口仅能排入"原因
    expl = r.explanations[1]
    assert (expl.schedule_reason == "") or ("仅能排入" in expl.schedule_reason)


# ---------- 数据库集成 ----------
@pytest.fixture()
def db_session():
    import sqlalchemy
    from sqlalchemy.pool import StaticPool

    engine = sqlalchemy.create_engine(
        DATABASE_URL.replace("nightplan", "nightplan_test"),
        poolclass=StaticPool,
    )
    from app.database import Base
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    from sqlalchemy.orm import Session

    db = Session(engine)
    yield db
    db.close()


def _make_night(db, with_targets=True):
    site = Site(name="测试站", latitude_deg=40.4, longitude_deg=117.6,
                elevation_m=900, timezone="Asia/Shanghai")
    db.add(site)
    db.flush()
    p = Proposal(code="T1", pi_name="测试员", title="t")
    db.add(p)
    db.flush()
    t1 = Target(proposal_id=p.id, name="M31", ra_deg=10.6847, dec_deg=41.2691,
                filter_name="V", exposure_seconds=600, readout_seconds=60,
                requested_frames=2, priority=1)
    t2 = Target(proposal_id=p.id, name="omegaCen", ra_deg=201.697, dec_deg=-47.4795,
                filter_name="V", exposure_seconds=600, readout_seconds=60,
                requested_frames=2, priority=3)
    db.add_all([t1, t2])
    db.flush()
    night = Night(site_id=site.id, night_date="2026-09-18")
    db.add(night)
    db.flush()
    from app.models import NightTarget
    db.add_all([NightTarget(night_id=night.id, target_id=t1.id),
                NightTarget(night_id=night.id, target_id=t2.id)])
    db.commit()
    return night, t1, t2


def test_service_initial_version_and_feasibility(db_session):
    night, t1, t2 = _make_night(db_session)
    ver = service.create_version(
        db_session, night, trigger="initial", reason="init"
    )
    assert ver.version == 1
    assert ver.feasibility[str(t1.id)]["feasible"] is True
    assert ver.feasibility[str(t2.id)]["feasible"] is False
    assert any("高度角" in r for r in ver.feasibility[str(t2.id)]["reasons"])
    m31_sci = [b for b in ver.blocks if b.kind == "science" and b.target_id == t1.id]
    assert len(m31_sci) == 2


def test_service_weather_replan_quota_not_double_counted(db_session):
    night, t1, t2 = _make_night(db_session)
    v1 = service.create_version(db_session, night, trigger="initial", reason="init")
    # 在第一帧结束后、第二帧进行中插入天气
    sci = sorted(
        [b for b in v1.blocks if b.kind == "science" and b.target_id == t1.id],
        key=lambda b: b.start_utc,
    )
    cut = sci[0].end_utc + timedelta(minutes=1)
    service.complete_until(db_session, night, v1, cut)
    db_session.add(
        WeatherInterval(
            night_id=night.id, start_utc=cut,
            end_utc=cut + timedelta(minutes=40), kind="cloud", source="test",
        )
    )
    db_session.flush()
    service.interrupt_planned_at(db_session, v1, cut)
    v2 = service.create_version(
        db_session, night, trigger="weather", reason="cloud", as_of=cut + timedelta(minutes=40)
    )
    # 台账中第 1 帧只有一条记录；新版本仍排 frame_seq=2
    acq = db_session.query(AcquiredFrame).filter_by(night_id=night.id).all()
    assert [(a.target_id, a.frame_seq) for a in acq] == [(t1.id, 1)]
    v2_sci = [b for b in v2.blocks if b.kind == "science" and b.target_id == t1.id]
    assert any(b.frame_seq == 2 and b.status == "planned" for b in v2_sci)
    assert v2.summary["diff_from_previous"]["moved"] >= 0


def test_service_manual_override_preserved(db_session):
    night, t1, t2 = _make_night(db_session)
    v1 = service.create_version(db_session, night, trigger="initial", reason="init")
    pin_at = datetime(2026, 9, 18, 17, 0, tzinfo=timezone.utc)
    db_session.add(
        Override(
            night_id=night.id, version_id=v1.id, target_id=t1.id, action="pin",
            payload={
                "start": pin_at.isoformat(),
                "end": (pin_at + timedelta(minutes=12)).isoformat(),
                "frame_seq": 2,
            },
            reason="人工测试锁定原因",
            created_by="tester",
        )
    )
    db_session.flush()
    v2 = service.create_version(
        db_session, night, trigger="manual", reason="pin",
        created_by="tester", as_of=pin_at,
    )
    pinned = [
        b for b in v2.blocks
        if b.target_id == t1.id and b.frame_seq == 2 and b.note
    ]
    assert pinned and "锁定" in pinned[0].note
    ov = db_session.query(Override).filter_by(night_id=night.id).one()
    assert ov.reason == "人工测试锁定原因"
    assert v2.version == 2
