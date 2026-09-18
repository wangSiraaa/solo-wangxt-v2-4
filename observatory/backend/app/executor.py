"""夜间执行模拟：把计划推进到某个时刻，完成曝光则落 Frame。

这是“已经采集的帧不得再次占用申请额度”的唯一落地点：
* ends_at <= as_of 且不撞坏天气的曝光 -> completed 且创建 Frame
  （冻结自历史版本的 carries_frame 副本不再重复建帧）；
* starts_at < as_of < ends_at 且撞坏天气 -> interrupted，不建帧、不占额度；
* 其余未来动作保持 pending，交由重排处理。

调用方只允许推进本夜最新版本——旧版本只是留档的计划，执行已被后续版本取代。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import intervals as iv
from .config import READOUT_SECONDS
from .models import (
    ActionKind, ActionStatus, Frame, Night, PlanVersion,
)


def ensure_latest(db: Session, version: PlanVersion) -> None:
    latest = db.scalar(
        select(PlanVersion).where(PlanVersion.night_id == version.night_id)
        .order_by(PlanVersion.version.desc()).limit(1)
    )
    if latest is not None and latest.id != version.id:
        raise HTTPException(
            409, f"v{version.version} 不是最新版本（最新 v{latest.version}），"
                 "执行只能推进最新计划")


def advance(db: Session, version: PlanVersion, as_of: datetime) -> dict:
    """把指定版本的动作状态推进到 as_of；返回统计。"""
    night = db.get(Night, version.night_id)
    bad = iv.merge([
        (s.starts_at, s.ends_at) for s in night.weather_samples if not s.usable
    ])

    completed = interrupted = in_progress = frames_created = 0
    for act in version.actions:
        if act.kind != ActionKind.exposure:
            if act.status == ActionStatus.pending and act.ends_at <= as_of:
                act.status = ActionStatus.completed
            elif act.status == ActionStatus.pending and act.starts_at < as_of:
                act.status = ActionStatus.in_progress
            continue

        if act.status in (ActionStatus.completed, ActionStatus.interrupted):
            if act.status == ActionStatus.completed:
                completed += 1  # 统计含冻结的历史完成帧
            continue

        # 曝光 + 读出都完成才算成功采集；读出期撞坏天气则整帧作废
        occupy_end = act.ends_at + timedelta(seconds=READOUT_SECONDS)
        hit_bad = any(act.starts_at < b1 and b0 < occupy_end for b0, b1 in bad)
        if occupy_end <= as_of and not hit_bad:
            act.status = ActionStatus.completed
            completed += 1
            if not act.carries_frame and not db.scalar(
                    select(Frame).where(Frame.action_id == act.id)):
                db.add(Frame(
                    target_id=act.target_id, night_id=night.id, action_id=act.id,
                    filter=act.filter, exposure_sec=act.duration_sec,
                    acquired_at=act.ends_at,
                ))
                frames_created += 1
        elif act.starts_at < as_of < occupy_end and hit_bad:
            # 曝光或读出进行中撞坏天气：中断，帧不计、不占额度
            act.status = ActionStatus.interrupted
            interrupted += 1
        elif act.starts_at < as_of < occupy_end:
            act.status = ActionStatus.in_progress
            in_progress += 1
        # starts_at >= as_of：未开始，保持 pending，交由重排处理

    db.flush()
    return {
        "as_of": as_of.isoformat(),
        "completed_exposures": completed,
        "interrupted_exposures": interrupted,
        "in_progress": in_progress,
        "frames_created": frames_created,
    }
