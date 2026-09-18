"""Pydantic 请求/响应模式。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def _ensure_utc(v: datetime) -> datetime:
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc)


class TargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    ra_hours: float | None = None          # 赤经（小时），与 ra_deg 二选一
    ra_deg: float | None = None
    dec_deg: float = Field(ge=-90, le=90)
    filter: str
    exposure_sec: float = Field(gt=0, le=7200)
    requested_frames: int = Field(default=1, ge=1, le=10000)
    min_altitude: float | None = Field(default=None, ge=0, le=90)
    min_moon_sep: float | None = Field(default=None, ge=0, le=180)
    priority: int = 100

    @field_validator("filter")
    @classmethod
    def _filter_known(cls, v: str) -> str:
        from .config import FILTERS
        if v not in FILTERS:
            raise ValueError(f"未知滤镜 {v}，可选：{sorted(FILTERS)}")
        return v

    @property
    def ra(self) -> float:
        if self.ra_deg is not None:
            return self.ra_deg % 360.0
        if self.ra_hours is not None:
            return (self.ra_hours * 15.0) % 360.0
        raise ValueError("ra_hours 与 ra_deg 必须提供一个")


class TargetOut(BaseModel):
    id: str
    proposal_id: str
    name: str
    ra_deg: float
    dec_deg: float
    filter: str
    exposure_sec: float
    requested_frames: int
    min_altitude: float | None
    min_moon_sep: float | None
    priority: int

    class Config:
        from_attributes = True


class ProposalIn(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    pi: str = Field(min_length=1, max_length=128)
    title: str = ""
    awarded_frames: int = Field(ge=0)
    targets: list[TargetIn] = Field(min_length=1)


class ProposalOut(BaseModel):
    id: str
    code: str
    pi: str
    title: str
    awarded_frames: int
    status: str
    targets: list[TargetOut]

    class Config:
        from_attributes = True


class NightIn(BaseModel):
    local_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    height_m: float = 0.0
    timezone: str = "UTC"
    note: str = ""


class NightOut(BaseModel):
    id: str
    local_date: str
    lat: float
    lon: float
    height_m: float
    timezone: str
    note: str

    class Config:
        from_attributes = True


class WeatherIn(BaseModel):
    starts_at: datetime
    ends_at: datetime
    usable: bool
    cloud_pct: float = Field(default=0.0, ge=0, le=100)
    note: str = ""

    @field_validator("starts_at", "ends_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


class WeatherOut(WeatherIn):
    id: str

    class Config:
        from_attributes = True


class VisibilityRequest(BaseModel):
    night_id: str
    proposal_id: str | None = None
    target_ids: list[str] | None = None


class GeneratePlanIn(BaseModel):
    trigger: Literal["initial", "weather", "manual", "completion"] = "initial"
    reason: str = ""
    as_of: datetime | None = None
    # 直接随请求附带的人工调整（会先落 ManualAdjustment 再排程）
    adjustments: list["AdjustmentIn"] | None = None

    @field_validator("as_of")
    @classmethod
    def _utc(cls, v: datetime | None) -> datetime | None:
        return _ensure_utc(v) if v else v


class AdjustmentIn(BaseModel):
    target_id: str | None = None
    kind: Literal["pin", "drop", "reorder", "override_constraint"]
    reason: str = Field(min_length=1)
    author: str = "scientist-on-duty"
    payload: dict[str, Any] = Field(default_factory=dict)


class AdvanceIn(BaseModel):
    as_of: datetime
    # 推进后是否立即用最新天气重排未开始的曝光
    reschedule: bool = True
    reason: str = "天气更新：缩短可用窗口，重排未开始曝光"

    @field_validator("as_of")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _ensure_utc(v)


class ActionOut(BaseModel):
    id: str
    sequence: int
    target_id: str | None
    kind: str
    filter: str | None
    starts_at: datetime
    ends_at: datetime
    duration_sec: float
    status: str
    lineage_id: str
    carries_frame: bool = False
    detail: str

    class Config:
        from_attributes = True


class AdjustmentOut(BaseModel):
    id: str
    target_id: str | None
    kind: str
    reason: str
    author: str
    payload: dict[str, Any]
    created_at: datetime

    class Config:
        from_attributes = True


class FrameOut(BaseModel):
    id: str
    target_id: str
    filter: str
    exposure_sec: float
    acquired_at: datetime

    class Config:
        from_attributes = True


class VersionOut(BaseModel):
    id: str
    version: int
    trigger: str
    reason: str
    created_at: datetime
    unscheduled: list[dict[str, Any]]
    night_summary: dict[str, Any]
    actions: list[ActionOut]
    adjustments: list[AdjustmentOut]

    class Config:
        from_attributes = True


class TimelineOut(BaseModel):
    night: NightOut
    summary: dict[str, Any]
    weather: list[WeatherOut]
    versions: list[VersionOut]
    frames: list[FrameOut]
    targets: list[TargetOut]
    visibility: list[dict[str, Any]]


GeneratePlanIn.model_rebuild()
