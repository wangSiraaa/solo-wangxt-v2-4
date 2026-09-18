"""观测夜、天气、重排、人工调整与执行模拟接口。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import astronomy, service
from ..database import get_db
from ..models import (
    AcquiredFrame,
    Night,
    NightTarget,
    Override,
    PlanVersion,
    Site,
    Target,
    WeatherInterval,
)
from ..schemas import (
    AddTargetsIn,
    CompleteIn,
    NightIn,
    NightOut,
    OverrideIn,
    OverrideOut,
    SimulateIn,
    TimelineOut,
    WeatherIn,
    WeatherOut,
)

router = APIRouter(prefix="/nights", tags=["nights"])


def _get_night(db: Session, night_id: int) -> Night:
    night = db.get(Night, night_id)
    if night is None:
        raise HTTPException(404, "观测夜不存在")
    return night


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.post("", response_model=NightOut)
def create_night(body: NightIn, db: Session = Depends(get_db)):
    site = db.get(Site, body.site_id)
    if site is None:
        raise HTTPException(404, "站点不存在")
    exists = db.scalar(
        select(Night).where(
            Night.site_id == body.site_id, Night.night_date == body.night_date
        )
    )
    if exists:
        raise HTTPException(409, "该站点此观测夜已存在")
    night = Night(site_id=body.site_id, night_date=body.night_date)
    db.add(night)
    db.flush()
    for tid in body.target_ids:
        if db.get(Target, tid) is None:
            raise HTTPException(404, f"目标 {tid} 不存在")
        db.add(NightTarget(night_id=night.id, target_id=tid))
    db.commit()
    db.refresh(night)
    return night


@router.get("", response_model=list[NightOut])
def list_nights(db: Session = Depends(get_db)):
    return list(db.scalars(select(Night).order_by(Night.id.desc())))


@router.post("/{night_id}/targets", response_model=NightOut)
def add_targets(night_id: int, body: AddTargetsIn, db: Session = Depends(get_db)):
    night = _get_night(db, night_id)
    have = {
        r.target_id
        for r in db.scalars(
            select(NightTarget).where(NightTarget.night_id == night_id)
        )
    }
    for tid in body.target_ids:
        if db.get(Target, tid) is None:
            raise HTTPException(404, f"目标 {tid} 不存在")
        if tid not in have:
            db.add(NightTarget(night_id=night_id, target_id=tid))
    db.commit()
    db.refresh(night)
    return night


@router.post("/{night_id}/plan")
def make_initial_plan(night_id: int, db: Session = Depends(get_db)):
    """生成第 1 版计划（initial），含各目标可行性解释。"""
    night = _get_night(db, night_id)
    if night.status == "completed":
        raise HTTPException(409, "该观测夜已结束（completed），不可再排")
    if service._latest_version(db, night) is not None:
        raise HTTPException(409, "初始计划已存在；天气/人工调整应走重排接口")
    ver = service.create_version(
        db, night, trigger="initial", reason="值班科学家生成初始夜间计划"
    )
    return {"version": ver.version, "summary": ver.summary}


@router.get("/{night_id}/versions")
def list_versions(night_id: int, db: Session = Depends(get_db)):
    night = _get_night(db, night_id)
    return [
        {
            "version": v.version,
            "trigger": v.trigger,
            "reason": v.reason,
            "created_by": v.created_by,
            "created_at": v.created_at,
            "summary": v.summary,
        }
        for v in night.versions
    ]


@router.get("/{night_id}/timeline", response_model=TimelineOut)
def timeline(night_id: int, db: Session = Depends(get_db)):
    night = _get_night(db, night_id)
    site = db.get(Site, night.site_id)
    site_info = service._site_info(site)
    _, (start, end) = service.night_utc_bounds(db, night)
    dark = astronomy.sun_below_intervals(site_info, start, end)

    acquired = [
        {
            "id": f.id,
            "target_id": f.target_id,
            "frame_seq": f.frame_seq,
            "filter_name": f.filter_name,
            "exposure_seconds": f.exposure_seconds,
            "version_id": f.version_id,
            "acquired_at": f.acquired_at,
        }
        for f in db.scalars(
            select(AcquiredFrame)
            .where(AcquiredFrame.night_id == night.id)
            .order_by(AcquiredFrame.acquired_at)
        )
    ]
    return {
        "night": night,
        "site": site,
        "acquired_frames": acquired,
        "overrides": db.scalars(
            select(Override)
            .where(Override.night_id == night.id)
            .order_by(Override.created_at)
        ).all(),
        "weather": db.scalars(
            select(WeatherInterval)
            .where(WeatherInterval.night_id == night.id)
            .order_by(WeatherInterval.start_utc)
        ).all(),
        "dark_intervals": [list(x) for x in dark],
        "versions": night.versions,
    }


@router.post("/{night_id}/weather", response_model=WeatherOut)
def add_weather(night_id: int, body: WeatherIn, db: Session = Depends(get_db)):
    night = _get_night(db, night_id)
    w = WeatherInterval(
        night_id=night.id,
        start_utc=_utc(body.start_utc),
        end_utc=_utc(body.end_utc),
        kind=body.kind,
        source=body.source,
        note=body.note,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


@router.post("/{night_id}/simulate-weather", response_model=dict)
def simulate_weather(night_id: int, body: SimulateIn, db: Session = Depends(get_db)):
    """演示用：在 at_utc 录入天气样本，打断跨点计划块，并重排未开始曝光。

    - 跨 at_utc 的计划曝光标记 interrupted（曝光未完成，不计入已采集额度）
    - at_utc 之前已结束的曝光照常登记为已采集帧
    - 新版本只重排 at_utc 之后未开始的曝光，已采集帧不重复占用额度
    """
    night = _get_night(db, night_id)
    prev = service._latest_version(db, night)
    if prev is None:
        raise HTTPException(409, "请先生成初始计划")
    at = _utc(body.at_utc)

    for w in (body.weather or []):
        db.add(
            WeatherInterval(
                night_id=night.id,
                start_utc=_utc(w.start_utc),
                end_utc=_utc(w.end_utc),
                kind=w.kind,
                source=w.source or "sample",
                note=w.note,
            )
        )
    db.flush()

    acquired = service.complete_until(db, night, prev, at)
    interrupted = service.interrupt_planned_at(db, prev, at)

    reason = body.note or (
        f"天气样本于 {at.isoformat()} 缩短窗口："
        f"登记完成帧 {len(acquired)}，中断块 {len(interrupted)}，"
        "仅重排未开始曝光（已采集帧不再占用额度）"
    )
    ver = service.create_version(
        db,
        night,
        trigger="weather",
        reason=reason,
        created_by="weather-sample",
        as_of=at,
    )
    return {
        "version": ver.version,
        "new_frames_acquired": len(acquired),
        "interrupted_blocks": len(interrupted),
        "summary": ver.summary,
    }


@router.post("/{night_id}/overrides", response_model=OverrideOut)
def add_override(night_id: int, body: OverrideIn, db: Session = Depends(get_db)):
    """值班科学家人工干预：

    - hold：封锁一段时间（如设备故障），必须填原因
    - pin ：把某目标某一帧锁定到指定时段，必须填原因
    调整生成 manual 版本，原因随版本与 override 审计一起保留。
    """
    night = _get_night(db, night_id)
    prev = service._latest_version(db, night)
    if prev is None:
        raise HTTPException(409, "请先生成初始计划")

    payload: dict = {}
    if body.action == "pin":
        if body.target_id is None or body.start_utc is None or body.end_utc is None:
            raise HTTPException(422, "pin 需要 target_id / start_utc / end_utc")
        if db.get(Target, body.target_id) is None:
            raise HTTPException(404, "目标不存在")
        payload = {
            "start": _utc(body.start_utc).isoformat(),
            "end": _utc(body.end_utc).isoformat(),
            "frame_seq": body.frame_seq or 1,
        }
        as_of = _utc(body.start_utc)
    else:  # hold
        if body.start_utc is None or body.end_utc is None:
            raise HTTPException(422, "hold 需要 start_utc / end_utc")
        payload = {
            "start": _utc(body.start_utc).isoformat(),
            "end": _utc(body.end_utc).isoformat(),
        }
        as_of = _utc(body.start_utc)

    ov = Override(
        night_id=night.id,
        version_id=prev.id,
        target_id=body.target_id,
        action=body.action,
        payload=payload,
        reason=body.reason,
        created_by=body.created_by,
    )
    db.add(ov)
    db.flush()

    ver = service.create_version(
        db,
        night,
        trigger="manual",
        reason=f"人工{('锁定' if body.action == 'pin' else '占用')}：{body.reason}",
        created_by=body.created_by,
        as_of=as_of,
    )
    db.refresh(ov)
    return ov


@router.post("/{night_id}/complete", response_model=dict)
def complete_observations(night_id: int, body: CompleteIn, db: Session = Depends(get_db)):
    """推进执行：登记 at_utc 前已结束的曝光帧（不重排）。"""
    night = _get_night(db, night_id)
    prev = service._latest_version(db, night)
    if prev is None:
        raise HTTPException(409, "请先生成初始计划")
    at = _utc(body.at_utc)
    frames = service.complete_until(db, night, prev, at)
    return {
        "version": prev.version,
        "new_frames_acquired": len(frames),
        "total_acquired": db.query(AcquiredFrame)
        .filter(AcquiredFrame.night_id == night.id)
        .count(),
    }


@router.post("/{night_id}/finish", response_model=NightOut)
def finish_night(night_id: int, db: Session = Depends(get_db)):
    """夜结束：确认所有已完成帧并把观测夜置为 completed。"""
    night = _get_night(db, night_id)
    prev = service._latest_version(db, night)
    if prev is None:
        raise HTTPException(409, "该夜还没有计划")
    night.status = "completed"
    db.commit()
    db.refresh(night)
    return night

