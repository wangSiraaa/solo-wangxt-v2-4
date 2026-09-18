"""数据库模型：提案、目标、观测夜、计划版本、计划动作、帧、天气、人工调整。"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Enum, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, TZDateTime


def _uuid() -> str:
    return uuid.uuid4().hex


class ProposalStatus(str, enum.Enum):
    submitted = "submitted"
    scheduled = "scheduled"
    completed = "completed"
    cancelled = "cancelled"


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    pi: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(256), default="")
    # 申请总帧数（跨全部目标）；已采集帧会占用额度
    awarded_frames: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[ProposalStatus] = mapped_column(
        Enum(ProposalStatus, native_enum=False, length=16), default=ProposalStatus.submitted
    )
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(timezone.utc))

    targets: Mapped[list["Target"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan", order_by="Target.created_at"
    )


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    proposal_id: Mapped[str] = mapped_column(ForeignKey("proposals.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    ra_deg: Mapped[float] = mapped_column(Float)          # ICRS 赤经（度）
    dec_deg: Mapped[float] = mapped_column(Float)         # ICRS 赤纬（度）
    filter: Mapped[str] = mapped_column(String(16))
    exposure_sec: Mapped[float] = mapped_column(Float)    # 单帧曝光
    requested_frames: Mapped[int] = mapped_column(Integer, default=1)
    # 目标级约束；为空时使用站点默认值
    min_altitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_moon_sep: Mapped[float | None] = mapped_column(Float, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # 数值越小越优先
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(timezone.utc))

    proposal: Mapped[Proposal] = relationship(back_populates="targets")


class Night(Base):
    __tablename__ = "nights"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # 本地历日（观测夜开始日期，YYYY-MM-DD）
    local_date: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    height_m: Mapped[float] = mapped_column(Float, default=0.0)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(timezone.utc))

    versions: Mapped[list["PlanVersion"]] = relationship(
        back_populates="night", cascade="all, delete-orphan", order_by="PlanVersion.version"
    )
    weather_samples: Mapped[list["WeatherSample"]] = relationship(
        back_populates="night", cascade="all, delete-orphan", order_by="WeatherSample.starts_at"
    )


class WeatherSample(Base):
    """天气时段：usable=false 时望远镜关闭，窗口被缩短；只能重排未开始的曝光。"""
    __tablename__ = "weather_samples"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    night_id: Mapped[str] = mapped_column(ForeignKey("nights.id", ondelete="CASCADE"), index=True)
    starts_at: Mapped[datetime] = mapped_column(TZDateTime, index=True)
    ends_at: Mapped[datetime] = mapped_column(TZDateTime)
    usable: Mapped[bool] = mapped_column(Boolean, default=True)
    cloud_pct: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(String(256), default="")

    night: Mapped[Night] = relationship(back_populates="weather_samples")


class PlanVersion(Base):
    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("night_id", "version", name="uq_night_version"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    night_id: Mapped[str] = mapped_column(ForeignKey("nights.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(32), default="initial")
    # initial / weather / manual / completion
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(timezone.utc))
    # 未排入目标的不可执行/超额原因快照，供前端解释
    unscheduled: Mapped[list] = mapped_column(JSON, default=list)
    # 晨昏蒙影边界等夜间摘要
    night_summary: Mapped[dict] = mapped_column(JSON, default=dict)

    night: Mapped[Night] = relationship(back_populates="versions")
    actions: Mapped[list["PlanAction"]] = relationship(
        back_populates="plan_version", cascade="all, delete-orphan",
        order_by="PlanAction.sequence",
    )
    adjustments: Mapped[list["ManualAdjustment"]] = relationship(
        back_populates="plan_version", cascade="all, delete-orphan",
    )


class ActionKind(str, enum.Enum):
    setup = "setup"       # 设备准备/找星
    filter_change = "filter_change"
    exposure = "exposure"


class ActionStatus(str, enum.Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    interrupted = "interrupted"   # 因天气中断（曝光未完成，帧不计、不占额度）


class PlanAction(Base):
    __tablename__ = "plan_actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    plan_version_id: Mapped[str] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[str | None] = mapped_column(
        ForeignKey("targets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[ActionKind] = mapped_column(Enum(ActionKind, native_enum=False, length=16))
    filter: Mapped[str | None] = mapped_column(String(16), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(TZDateTime)
    ends_at: Mapped[datetime] = mapped_column(TZDateTime)
    duration_sec: Mapped[float] = mapped_column(Float)
    status: Mapped[ActionStatus] = mapped_column(
        Enum(ActionStatus, native_enum=False, length=16), default=ActionStatus.pending
    )
    # 关联到上一版本中的同一逻辑动作（用于中断后续接展示）
    lineage_id: Mapped[str] = mapped_column(String(32), index=True)
    # 冻结自历史版本的已完成动作，其物理帧已在原动作上创建：不再重复建帧
    carries_frame: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[str] = mapped_column(String(256), default="")

    plan_version: Mapped[PlanVersion] = relationship(back_populates="actions")


class Frame(Base):
    """已采集帧——唯一占用申请额度的记录。重排绝不复制 Frame。"""
    __tablename__ = "frames"
    __table_args__ = (UniqueConstraint("action_id", name="uq_frame_action"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    target_id: Mapped[str] = mapped_column(ForeignKey("targets.id"), index=True)
    night_id: Mapped[str] = mapped_column(ForeignKey("nights.id"), index=True)
    action_id: Mapped[str] = mapped_column(ForeignKey("plan_actions.id"), index=True)
    filter: Mapped[str] = mapped_column(String(16))
    exposure_sec: Mapped[float] = mapped_column(Float)
    acquired_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(timezone.utc))


class ManualAdjustment(Base):
    """值班科学家的人工修改及其原因，随计划版本保留。"""
    __tablename__ = "manual_adjustments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    plan_version_id: Mapped[str] = mapped_column(
        ForeignKey("plan_versions.id", ondelete="CASCADE"), index=True
    )
    target_id: Mapped[str | None] = mapped_column(
        ForeignKey("targets.id", ondelete="SET NULL"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(32))   # pin / drop / reorder / override_constraint ...
    reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=lambda: datetime.now(timezone.utc))
    author: Mapped[str] = mapped_column(String(128), default="scientist-on-duty")

    plan_version: Mapped[PlanVersion] = relationship(back_populates="adjustments")
