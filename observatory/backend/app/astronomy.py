"""基于 Astropy 的可见性计算：晨昏蒙影、高度角、月距、采样曲线。

所有对外时间均为带时区的 UTC datetime。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import warnings

import astropy.units as u
import numpy as np
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body, get_sun
from astropy.time import Time
from astropy.utils.exceptions import AstropyWarning

from . import intervals as iv

warnings.filterwarnings("ignore", category=AstropyWarning)


def make_location(lat: float, lon: float, height_m: float = 0.0) -> EarthLocation:
    return EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=height_m * u.m)


def to_astropy_time(times: list[datetime] | datetime) -> Time:
    if isinstance(times, datetime):
        times = [times]
    return Time([t.astimezone(timezone.utc) for t in times])


def _sun_alt(t: datetime, location: EarthLocation) -> float:
    aa = AltAz(obstime=to_astropy_time(t), location=location)
    return float(get_sun(to_astropy_time(t)).transform_to(aa).alt.deg[0])


def _crossing(f, lo: datetime, hi: datetime, target: float,
              decreasing: bool, tol_sec: float = 5.0) -> datetime | None:
    """在 [lo, hi] 内二分求 f(t)=target；要求端点异号且方向正确。"""
    flo, fhi = f(lo) - target, f(hi) - target
    if flo == 0:
        return lo
    if decreasing:
        ok = flo > 0 > fhi
    else:
        ok = flo < 0 < fhi
    if not ok:
        return None
    a, b = lo, hi
    while (b - a).total_seconds() > tol_sec:
        m = a + (b - a) / 2
        fm = f(m) - target
        if (fm > 0) if decreasing else (fm < 0):
            a = m
        else:
            b = m
    return a + (b - a) / 2


@dataclass
class NightWindows:
    sunset: datetime | None
    sunrise: datetime | None
    civil_dark: list[tuple[datetime, datetime]]       # 太阳 < -6°
    nautical_dark: list[tuple[datetime, datetime]]    # 太阳 < -12°
    astronomical_dark: list[tuple[datetime, datetime]]  # 太阳 < -18°
    summary: dict

    def dark_for_level(self, level: str) -> list[tuple[datetime, datetime]]:
        return {
            "civil": self.civil_dark,
            "nautical": self.nautical_dark,
            "astronomical": self.astronomical_dark,
        }[level]

    @property
    def horizon(self) -> tuple[datetime, datetime] | None:
        """可排程边界：日落到日出（设备准备可在民用蒙影进行）。"""
        if self.sunset and self.sunrise:
            return self.sunset, self.sunrise
        return None


def night_windows(local_date: str, location: EarthLocation, tz_name: str) -> NightWindows:
    """计算给定观测夜（本地历日）的晨昏蒙影边界。"""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    d = datetime.fromisoformat(local_date).replace(tzinfo=tz)
    noon = d.replace(hour=12, minute=0, second=0, microsecond=0)
    noon_utc = noon.astimezone(timezone.utc)

    def sun(t: datetime) -> float:
        return _sun_alt(t, location)

    # 傍晚：本地正午 -> 次日本地正午，太阳下降
    evening_lo, evening_hi = noon_utc, noon_utc + timedelta(hours=24)
    # 凌晨：本地午夜前后；用傍晚窗口内的第二次交叉，单独在 [noon, noon+24] 求两个方向
    thresholds = [0.0, -6.0, -12.0, -18.0]
    evening_times: dict[float, datetime | None] = {}
    morning_times: dict[float, datetime | None] = {}

    # 粗扫（2 分钟步长）找所有过零段，再分别二分
    step = timedelta(minutes=2)
    grid_t = [evening_lo + i * step for i in range(int((evening_hi - evening_lo) / step) + 1)]
    sun_alts = _sun_alts_vector(grid_t, location)
    for target in thresholds:
        found_down = found_up = None
        for i in range(len(grid_t) - 1):
            a0, a1 = sun_alts[i] - target, sun_alts[i + 1] - target
            if a0 > 0 >= a1 and found_down is None:
                found_down = _crossing(sun, grid_t[i], grid_t[i + 1], target, decreasing=True)
            if a0 < 0 <= a1 and found_up is None:
                found_up = _crossing(sun, grid_t[i], grid_t[i + 1], target, decreasing=False)
        evening_times[target] = found_down
        morning_times[target] = found_up

    def pair(target: float) -> list[tuple[datetime, datetime]]:
        a, b = evening_times[target], morning_times[target]
        return [(a, b)] if a and b else []

    summary = {
        "local_date": local_date,
        "timezone": tz_name,
        "sunset": evening_times[0.0].isoformat() if evening_times[0.0] else None,
        "sunrise": morning_times[0.0].isoformat() if morning_times[0.0] else None,
        "civil_start": evening_times[-6.0].isoformat() if evening_times[-6.0] else None,
        "civil_end": morning_times[-6.0].isoformat() if morning_times[-6.0] else None,
        "nautical_start": evening_times[-12.0].isoformat() if evening_times[-12.0] else None,
        "nautical_end": morning_times[-12.0].isoformat() if morning_times[-12.0] else None,
        "astro_start": evening_times[-18.0].isoformat() if evening_times[-18.0] else None,
        "astro_end": morning_times[-18.0].isoformat() if morning_times[-18.0] else None,
    }
    return NightWindows(
        sunset=evening_times[0.0],
        sunrise=morning_times[0.0],
        civil_dark=pair(-6.0),
        nautical_dark=pair(-12.0),
        astronomical_dark=pair(-18.0),
        summary=summary,
    )


def _sun_alts_vector(times: list[datetime], location: EarthLocation) -> np.ndarray:
    t = to_astropy_time(times)
    return get_sun(t).transform_to(AltAz(obstime=t, location=location)).alt.deg


@dataclass
class TargetWindows:
    alt_windows: list
    moon_windows: list
    dark_windows: list
    feasible: list                      # 高度 ∩ 月距 ∩ 暗天光
    samples: list[dict]                 # 给前端画曲线
    min_sep: float
    min_alt: float
    moon_illumination: float


def target_windows(
    ra_deg: float,
    dec_deg: float,
    location: EarthLocation,
    horizon: tuple[datetime, datetime],
    dark: list[tuple[datetime, datetime]],
    min_alt: float,
    min_moon_sep: float,
    grid_step_sec: int = 60,
    sample_step_min: int = 10,
) -> TargetWindows:
    """计算单个目标在本夜的可执行窗口与采样曲线。"""
    start, end = horizon
    n = int((end - start).total_seconds() / grid_step_sec) + 1
    times = [start + timedelta(seconds=i * grid_step_sec) for i in range(n)]
    t = to_astropy_time(times)
    frame = AltAz(obstime=t, location=location)

    coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    aa = coord.transform_to(frame)
    alts = np.asarray(aa.alt.deg, dtype=float)

    moon = get_body("moon", t, location)
    seps = np.asarray(moon.separation(coord).deg, dtype=float)
    moon_alts = np.asarray(moon.transform_to(frame).alt.deg, dtype=float)
    sun_alts = _sun_alts_vector(times, location)

    alt_ok = alts >= min_alt
    moon_ok = seps >= min_moon_sep
    alt_w = _mask_intervals(times, alt_ok)
    moon_w = _mask_intervals(times, moon_ok)
    dark_w = list(dark)

    feasible = iv.intersect(iv.intersect(alt_w, moon_w), dark_w)

    # 曲线采样（稀疏）
    stride = max(1, sample_step_min * 60 // grid_step_sec)
    samples = [
        {
            "t": times[i].isoformat(),
            "alt": round(float(alts[i]), 2),
            "moon_sep": round(float(seps[i]), 2),
            "moon_alt": round(float(moon_alts[i]), 2),
            "sun_alt": round(float(sun_alts[i]), 2),
            "ok": bool(alt_ok[i] and moon_ok[i]),
        }
        for i in range(0, len(times), stride)
    ]
    illum = _moon_illumination(t[len(t) // 2])
    return TargetWindows(
        alt_windows=alt_w, moon_windows=moon_w, dark_windows=dark_w,
        feasible=feasible, samples=samples, min_sep=min_moon_sep,
        min_alt=min_alt, moon_illumination=illum,
    )


def _mask_intervals(times: list[datetime], mask: np.ndarray) -> list[tuple[datetime, datetime]]:
    """布尔序列 -> True 连续段（时间），端点线性内插到阈值，精度足够排程。"""
    out: list[tuple[datetime, datetime]] = []
    i = 0
    n = len(times)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append((times[i], times[j]))
            i = j + 1
        else:
            i += 1
    return out


def _moon_illumination(t: Time) -> float:
    """月面照亮比例 0~1（基于日月与地心月位置的相位角）。"""
    moon = get_body("moon", t)
    sun = get_sun(t)
    # 相位角：地球处月-日夹角，距离修正
    elongation = moon.separation(sun).rad
    d_me = moon.distance.to(u.m).value
    d_se = sun.distance.to(u.m).value
    phase = np.arccos(
        np.clip((d_me - d_se * np.cos(elongation)) /
                np.sqrt(d_se**2 + d_me**2 - 2 * d_se * d_me * np.cos(elongation)), -1, 1)
    )
    return round(float((1 + np.cos(phase)) / 2), 3)
