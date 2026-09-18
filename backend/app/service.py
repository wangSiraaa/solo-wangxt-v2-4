"""排程服务：把天文计算与调度引擎接到数据库，管理计划版本。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import astronomy, scheduler
from .models import (
    AcquiredFrame,
    Night,
    NightTarget,
    Override,
    PlanBlock,
    PlanVersion,
    Site,
    Target,
    WeatherInterval,
)

GRID = astronomy.GRID_MINUTES


class PlanningError(Exception):
    pass


def _site_info(site: Site) -> astronomy.SiteInfo:
    return astronomy.SiteInfo(
        latitude_deg=site.latitude_deg,
        longitude_deg=site.longitude_deg,
        elevation_m=site.elevation_m,
    )


def night_utc_bounds(db: Session, night: Night):
    site = db.get(Site, night.site_id)
    info = _site_info(site)
    return info, astronomy.night_window(info, night.night_date, site.timezone)


def _targets(db: Session, night: Night) -> list[Target]:
    rows = db.execute(
        select(Target)
        .join(NightTarget, NightTarget.target_id == Target.id)
        .where(NightTarget.night_id == night.id)
        .order_by(Target.priority, Target.id)
    ).scalars().all()
    return list(rows)


def _weather(db: Session, night: Night) -> list[tuple[datetime, datetime]]:
    rows = db.execute(
        select(WeatherInterval)
        .where(WeatherInterval.night_id == night.id)
        .order_by(WeatherInterval.start_utc)
    ).scalars().all()
    return [(_utc(r.start_utc), _utc(r.end_utc)) for r in rows]


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_feasibility(
    db: Session, night: Night
) -> tuple[astronomy.SiteInfo, datetime, datetime, list, list[Target], dict]:
    """返回 site, start, end, dark_intervals, targets, {target_id: feas_mask}。"""
    site_info, (start, end) = night_utc_bounds(db, night)
    times = astronomy.grid_times(start, end)
    dark_intervals = astronomy.sun_below_intervals(site_info, start, end)
    targets = _targets(db, night)
    masks: dict[int, np.ndarray] = {}
    for t in targets:
        alt, moon = astronomy.target_alt_moon_sep(
            site_info, t.ra_deg, t.dec_deg, times
        )
        dark_mask = np.zeros(len(times), dtype=bool)
        for a, b in dark_intervals:
            dark_mask |= np.array([a <= tt <= b for tt in times])
        masks[t.id] = (
            dark_mask
            & (alt >= t.min_altitude_deg)
            & (moon >= t.min_moon_separation_deg)
        )
    return site_info, start, end, dark_intervals, targets, masks


def _acquired(db: Session, night: Night) -> dict[int, int]:
    rows = db.execute(
        select(AcquiredFrame.target_id)
        .where(AcquiredFrame.night_id == night.id)
    ).scalars().all()
    counts: dict[int, int] = {}
    for tid in rows:
        counts[tid] = counts.get(tid, 0) + 1
    return counts


def _holds(db: Session, night: Night):
    rows = db.execute(
        select(Override).where(
            Override.night_id == night.id, Override.action == "hold"
        )
    ).scalars().all()
    out = []
    for r in rows:
        out.append((datetime.fromisoformat(r.payload["start"]),
                    datetime.fromisoformat(r.payload["end"])))
    return out


def _pins(db: Session, night: Night) -> list[scheduler.Pin]:
    rows = db.execute(
        select(Override).where(
            Override.night_id == night.id, Override.action == "pin"
        )
    ).scalars().all()
    return [
        scheduler.Pin(
            target_id=r.target_id,
            start=datetime.fromisoformat(r.payload["start"]),
            end=datetime.fromisoformat(r.payload["end"]),
            frame_seq=int(r.payload["frame_seq"]),
        )
        for r in rows
    ]


def _latest_version(db: Session, night: Night) -> PlanVersion | None:
    return db.execute(
        select(PlanVersion)
        .where(PlanVersion.night_id == night.id)
        .order_by(PlanVersion.version.desc())
    ).scalars().first()


def _carried_blocks(
    version: PlanVersion | None, as_of: datetime
) -> list[scheduler.ScheduledBlock]:
    """重排：把 as_of 之前已完成或被中断的块原样带入新版本。"""
    if version is None:
        return []
    out: list[scheduler.ScheduledBlock] = []
    for b in version.blocks:
        start, end = _utc(b.start_utc), _utc(b.end_utc)
        if b.status == "completed" and end <= as_of:
            out.append(
                scheduler.ScheduledBlock(
                    kind=b.kind,
                    target_id=b.target_id,
                    filter_name=b.filter_name,
                    start=start,
                    end=end,
                    frame_seq=b.frame_seq,
                    status=b.status,
                    note=b.note,
                )
            )
        elif b.status == "interrupted" and start < as_of <= end:
            # 跨中断点的块：曝光未完成、不计额度，但作为 interrupted 记录保留
            out.append(
                scheduler.ScheduledBlock(
                    kind=b.kind,
                    target_id=b.target_id,
                    filter_name=b.filter_name,
                    start=start,
                    end=as_of,
                    frame_seq=b.frame_seq,
                    status="interrupted",
                    note=b.note,
                )
            )
    return out


def create_version(
    db: Session,
    night: Night,
    *,
    trigger: str,
    reason: str = "",
    created_by: str = "scheduler",
    as_of: datetime | None = None,
    commit: bool = True,
) -> PlanVersion:
    """生成并持久化一个计划版本（initial / weather / manual）。"""
    site_info, start, end, dark_intervals, targets, masks = compute_feasibility(
        db, night
    )
    # 天空层面的不可行原因（高度角 / 月距 / 晨昏）
    assessments = {
        t.id: astronomy.assess_target(site_info, t, start, end, dark_intervals)
        for t in targets
    }
    weather = _weather(db, night)
    acquired = _acquired(db, night)
    prev = _latest_version(db, night)

    if as_of is None:
        as_of = start if prev is None else datetime.now(timezone.utc)
    as_of = _utc(as_of)

    carried = _carried_blocks(prev, as_of)
    ctx = scheduler.PlanContext(
        start=start,
        end=end,
        dark_intervals=dark_intervals,
        weather_intervals=weather,
        targets=targets,
        feas_masks=masks,
        sky_reasons={tid: f.reasons for tid, f in assessments.items()},
        acquired=acquired,
        as_of=as_of,
        carried=carried,
        pins=_pins(db, night),
        holds=_holds(db, night),
    )
    result = scheduler.build_plan(ctx)

    version_no = (prev.version + 1) if prev else 1
    ver = PlanVersion(
        night_id=night.id,
        version=version_no,
        trigger=trigger,
        reason=reason,
        created_by=created_by,
        feasibility={
            str(e.target_id): e.to_dict(result.visible[e.target_id])
            for e in result.explanations.values()
        },
        summary=result.summary
        | {"diff_from_previous": _diff(prev, result, targets)},
    )
    db.add(ver)
    db.flush()

    for b in result.blocks:
        db.add(
            PlanBlock(
                version_id=ver.id,
                target_id=b.target_id,
                kind=b.kind,
                status=b.status,
                frame_seq=b.frame_seq,
                filter_name=b.filter_name,
                start_utc=b.start,
                end_utc=b.end,
                note=b.note,
            )
        )
    if commit:
        db.commit()
        db.refresh(ver)
    return ver


def _diff(prev: PlanVersion | None, result: scheduler.PlanResult, targets) -> dict:
    """与上一版对比：搬移/新增/保留的科学块数。"""
    if prev is None:
        return {"moved": 0, "kept": 0, "new": result.summary["science_blocks"]}
    old = {
        (b.target_id, b.frame_seq): (_utc(b.start_utc), _utc(b.end_utc))
        for b in prev.blocks
        if b.kind == "science"
    }
    moved = kept = 0
    for b in result.blocks:
        if b.kind != "science" or b.status != "planned":
            continue
        key = (b.target_id, b.frame_seq)
        if key in old:
            if abs((b.start - old[key][0]).total_seconds()) >= GRID * 60:
                moved += 1
            else:
                kept += 1
    return {"moved": moved, "kept": kept, "new": 0}


def interrupt_planned_at(
    db: Session, version: PlanVersion, at: datetime
) -> list[PlanBlock]:
    """天气在 at 时刻打断：把跨越 at 的计划块标记 interrupted。"""
    at = _utc(at)
    changed = []
    for b in version.blocks:
        s, e = _utc(b.start_utc), _utc(b.end_utc)
        if b.status == "planned" and s < at < e:
            b.status = "interrupted"
            b.note = (b.note + " " if b.note else "") + "天气中断（曝光未完成，不计额度）"
            changed.append(b)
    db.commit()
    return changed


def complete_until(
    db: Session,
    night: Night,
    version: PlanVersion,
    at: datetime,
    *,
    allow_partial_block: bool = False,
) -> list[AcquiredFrame]:
    """把 at 之前已结束的 science 块登记为已采集帧；块状态置 completed。

    已登记过的 (target, frame_seq) 不会重复入库——申请额度只扣一次。
    """
    at = _utc(at)
    frames: list[AcquiredFrame] = []
    for b in version.blocks:
        end = _utc(b.end_utc)
        if end <= at and b.status == "planned":
            if b.kind in ("setup", "filter_change"):
                # 设备准备与滤镜切换按时完成（不产生申请额度）
                b.status = "completed"
                continue
            if b.kind != "science":
                continue
            b.status = "completed"
            exists = db.execute(
                select(AcquiredFrame).where(
                    AcquiredFrame.night_id == night.id,
                    AcquiredFrame.target_id == b.target_id,
                    AcquiredFrame.frame_seq == b.frame_seq,
                )
            ).scalars().first()
            if exists is None:
                target = db.get(Target, b.target_id)
                af = AcquiredFrame(
                    night_id=night.id,
                    target_id=b.target_id,
                    frame_seq=b.frame_seq,
                    filter_name=b.filter_name,
                    exposure_seconds=target.exposure_seconds,
                    version_id=version.id,
                    acquired_at=end,
                )
                db.add(af)
                frames.append(af)
    db.commit()
    return frames
