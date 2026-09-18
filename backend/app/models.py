"""ORM 模型：提案、目标、观测夜、计划版本、已采集帧与人工调整。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Site(Base):
    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    latitude_deg: Mapped[float] = mapped_column(Float)
    longitude_deg: Mapped[float] = mapped_column(Float)
    elevation_m: Mapped[float] = mapped_column(Float, default=0.0)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")

    nights: Mapped[list["Night"]] = relationship(back_populates="site")


class Proposal(Base):
    """观测提案：一个提案包含多个观测目标。"""

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    pi_name: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    targets: Mapped[list["Target"]] = relationship(
        back_populates="proposal", cascade="all, delete-orphan"
    )


class Target(Base):
    """申请人录入的观测目标与申请额度（requested_frames 帧）。"""

    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("proposals.id"))
    name: Mapped[str] = mapped_column(String(128))
    ra_deg: Mapped[float] = mapped_column(Float)          # ICRS 赤经 0..360
    dec_deg: Mapped[float] = mapped_column(Float)        # ICRS 赤纬 -90..90
    magnitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    filter_name: Mapped[str] = mapped_column(String(16))  # U/B/V/R/I ...
    exposure_seconds: Mapped[int] = mapped_column(Integer)
    readout_seconds: Mapped[int] = mapped_column(Integer, default=45)
    requested_frames: Mapped[int] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1 最高
    min_altitude_deg: Mapped[float] = mapped_column(Float, default=30.0)
    min_moon_separation_deg: Mapped[float] = mapped_column(Float, default=40.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    proposal: Mapped[Proposal] = relationship(back_populates="targets")


class Night(Base):
    """值班科学家选定的观测夜（站点本地日期，指傍晚所在日期）。"""

    __tablename__ = "nights"

    id: Mapped[int] = mapped_column(primary_key=True)
    site_id: Mapped[int] = mapped_column(ForeignKey("sites.id"))
    night_date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD（站点本地）
    status: Mapped[str] = mapped_column(String(16), default="planning")
    # planning -> active -> completed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    site: Mapped[Site] = relationship(back_populates="nights")
    night_targets: Mapped[list["NightTarget"]] = relationship(
        back_populates="night", cascade="all, delete-orphan"
    )
    weather: Mapped[list["WeatherInterval"]] = relationship(
        back_populates="night", cascade="all, delete-orphan"
    )
    versions: Mapped[list["PlanVersion"]] = relationship(
        back_populates="night",
        cascade="all, delete-orphan",
        order_by="PlanVersion.version",
    )

    __table_args__ = (UniqueConstraint("site_id", "night_date"),)


class NightTarget(Base):
    __tablename__ = "night_targets"
    __table_args__ = (UniqueConstraint("night_id", "target_id"),)

    night_id: Mapped[int] = mapped_column(ForeignKey("nights.id"), primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), primary_key=True)

    night: Mapped[Night] = relationship(back_populates="night_targets")


class WeatherInterval(Base):
    """天气中断区间（人工录入或来自本地天气样本文件）。"""

    __tablename__ = "weather_intervals"

    id: Mapped[int] = mapped_column(primary_key=True)
    night_id: Mapped[int] = mapped_column(ForeignKey("nights.id"))
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(String(32), default="cloud")
    source: Mapped[str] = mapped_column(String(32), default="manual")
    note: Mapped[str] = mapped_column(String(256), default="")

    night: Mapped[Night] = relationship(back_populates="weather")


class PlanVersion(Base):
    """计划版本：不可变快照。重排与人工调整都生成新版本。"""

    __tablename__ = "plan_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    night_id: Mapped[int] = mapped_column(ForeignKey("nights.id"))
    version: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(16))  # initial | weather | manual
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="scheduler")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # 每个目标的可行性解释（高度角 / 月距 / 晨昏 / 天气后无时间）
    feasibility: Mapped[dict] = mapped_column(JSONB, default=dict)
    # 版本摘要与与上一版差异（搬移的块数、未排上目标等）
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)

    night: Mapped[Night] = relationship(back_populates="versions")
    blocks: Mapped[list["PlanBlock"]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="PlanBlock.start_utc",
    )


class PlanBlock(Base):
    """时间轴上的一个块：设备准备 / 滤镜切换 / 科学曝光。"""

    __tablename__ = "plan_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("plan_versions.id"))
    target_id: Mapped[int | None] = mapped_column(
        ForeignKey("targets.id"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    # setup | filter_change | science；status 区分计划/已完成/中断
    status: Mapped[str] = mapped_column(String(16), default="planned")
    frame_seq: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filter_name: Mapped[str | None] = mapped_column(String(16), nullable=True)
    start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(String(256), default="")

    version: Mapped[PlanVersion] = relationship(back_populates="blocks")


class AcquiredFrame(Base):
    """已采集帧台账：重排只看剩余额度，已采集帧不会被重复计数。"""

    __tablename__ = "acquired_frames"
    __table_args__ = (
        UniqueConstraint("night_id", "target_id", "frame_seq", name="uq_frame_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    night_id: Mapped[int] = mapped_column(ForeignKey("nights.id"))
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"))
    frame_seq: Mapped[int] = mapped_column(Integer)  # 1-based，目标内帧号
    filter_name: Mapped[str] = mapped_column(String(16))
    exposure_seconds: Mapped[int] = mapped_column(Integer)
    version_id: Mapped[int] = mapped_column(ForeignKey("plan_versions.id"))
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Override(Base):
    """人工调整审计：谁、在哪个版本、为什么调整。"""

    __tablename__ = "overrides"

    id: Mapped[int] = mapped_column(primary_key=True)
    night_id: Mapped[int] = mapped_column(ForeignKey("nights.id"))
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("plan_versions.id"), nullable=True
    )
    target_id: Mapped[int | None] = mapped_column(
        ForeignKey("targets.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(32))  # pin | hold
    payload: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), default=dict)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(64), default="scientist")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
