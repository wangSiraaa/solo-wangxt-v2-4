"""请求 / 响应模型。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


# ---------- 站点 ----------
class SiteIn(BaseModel):
    name: str
    latitude_deg: float = Field(ge=-90, le=90)
    longitude_deg: float = Field(ge=-180, le=180)
    elevation_m: float = 0.0
    timezone: str = "Asia/Shanghai"


class SiteOut(SiteIn):
    id: int


# ---------- 提案 / 目标 ----------
class TargetIn(BaseModel):
    name: str
    ra_deg: float = Field(ge=0, lt=360)
    dec_deg: float = Field(ge=-90, le=90)
    magnitude: float | None = None
    filter_name: str = Field(pattern=r"^[UBVRIugrizJHKH]+$|^[a-zA-Z0-9_-]{1,16}$")
    exposure_seconds: int = Field(gt=0)
    readout_seconds: int = Field(default=45, ge=0)
    requested_frames: int = Field(gt=0)
    priority: int = Field(default=3, ge=1, le=9)
    min_altitude_deg: float = 30.0
    min_moon_separation_deg: float = 40.0


class TargetOut(TargetIn):
    id: int
    proposal_id: int
    acquired_frames: int = 0


class ProposalIn(BaseModel):
    code: str
    pi_name: str
    title: str = ""


class ProposalOut(ProposalIn):
    id: int
    created_at: datetime
    targets: list[TargetOut] = []


# ---------- 观测夜 ----------
class NightIn(BaseModel):
    site_id: int
    night_date: str  # YYYY-MM-DD
    target_ids: list[int] = []


class NightOut(BaseModel):
    id: int
    site_id: int
    night_date: str
    status: str
    created_at: datetime


class AddTargetsIn(BaseModel):
    target_ids: list[int]


# ---------- 天气 ----------
class WeatherIn(BaseModel):
    start_utc: datetime
    end_utc: datetime
    kind: str = "cloud"
    source: str = "manual"
    note: str = ""

    @field_validator("end_utc")
    @classmethod
    def _after_start(cls, v, info):
        s = info.data.get("start_utc")
        if s and v <= s:
            raise ValueError("end_utc 必须晚于 start_utc")
        return v


class WeatherOut(WeatherIn):
    id: int
    night_id: int


# ---------- 人工调整 ----------
class OverrideIn(BaseModel):
    action: str = Field(pattern="^(hold|pin)$")
    target_id: int | None = None  # pin 必填
    start_utc: datetime | None = None  # pin 必填，hold 必填
    end_utc: datetime | None = None
    frame_seq: int | None = None  # pin 必填
    reason: str = Field(min_length=1)
    created_by: str = "scientist"


class OverrideOut(BaseModel):
    id: int
    night_id: int
    target_id: int | None
    action: str
    payload: dict
    reason: str
    created_by: str
    created_at: datetime


# ---------- 执行模拟 ----------
class SimulateIn(BaseModel):
    at_utc: datetime
    weather: list[WeatherIn] | None = None
    note: str = ""


class CompleteIn(BaseModel):
    at_utc: datetime


# ---------- 时间轴输出 ----------
class BlockOut(BaseModel):
    id: int
    kind: str
    target_id: int | None
    filter_name: str | None
    frame_seq: int | None
    status: str
    start_utc: datetime
    end_utc: datetime
    note: str


class VersionOut(BaseModel):
    id: int
    version: int
    trigger: str
    reason: str
    created_by: str
    created_at: datetime
    feasibility: dict
    summary: dict
    blocks: list[BlockOut]


class TimelineOut(BaseModel):
    night: NightOut
    site: SiteOut
    acquired_frames: list[dict]
    overrides: list[OverrideOut]
    weather: list[WeatherOut]
    dark_intervals: list[list[datetime]]
    versions: list[VersionOut]
