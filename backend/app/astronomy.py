"""天文可见性计算：高度角、月距、晨昏窗口。

全部时间统一用 UTC；站点信息从数据库读出。
晨昏定义（太阳质心高度）：
  -12° 航海晨昏、-18° 天文晨昏。排程只允许在天文晨昏夜内进行科学曝光。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

import numpy as np
from astropy.coordinates import EarthLocation, SkyCoord, get_body, get_sun
from astropy.time import Time
from astropy import units as u

# 网格步长（分钟）：在网格点上采样高度角与月距
GRID_MINUTES = 2

SUN_ASTRONOMICAL_DEG = -18.0  # 天文晨昏阈值


@dataclass(frozen=True)
class SiteInfo:
    latitude_deg: float
    longitude_deg: float
    elevation_m: float

    def earth_location(self) -> EarthLocation:
        return EarthLocation(
            lat=self.latitude_deg * u.deg,
            lon=self.longitude_deg * u.deg,
            height=self.elevation_m * u.m,
        )


def night_window(
    site: SiteInfo,
    local_evening_date: str,
    tz_name: str = "Asia/Shanghai",
) -> tuple[datetime, datetime]:
    """站点本地傍晚日期（YYYY-MM-DD）-> UTC 区间 [当日12:00本地, 次日12:00本地)。

    用 26 小时的覆盖区间保证能完整捕捉到当地夜间的晨昏交叉时刻，
    实际可排程区间由 sun_below_intervals 裁剪。
    """
    tz = ZoneInfo(tz_name)
    noon_local = datetime.fromisoformat(local_evening_date + "T12:00:00").replace(
        tzinfo=tz
    )
    start = noon_local.astimezone(timezone.utc)
    end = start + timedelta(hours=26)
    return start, end


def grid_times(start: datetime, end: datetime, step_minutes: int = GRID_MINUTES):
    n = int((end - start).total_seconds() // (step_minutes * 60)) + 1
    return [start + timedelta(minutes=step_minutes * i) for i in range(n)]


def sun_alt_deg(site: SiteInfo, times: Iterable[datetime]) -> np.ndarray:
    t = Time(list(times), format="datetime", scale="utc")
    loc = site.earth_location()
    altaz = get_sun(t).transform_to(__altaz_frame(loc, t))
    return altaz.alt.deg


def sun_below_intervals(
    site: SiteInfo,
    start: datetime,
    end: datetime,
    alt_threshold_deg: float = SUN_ASTRONOMICAL_DEG,
) -> list[tuple[datetime, datetime]]:
    """太阳低于阈值的连续区间（天文黑夜窗口）。"""
    times = grid_times(start, end)
    below = sun_alt_deg(site, times) < alt_threshold_deg
    intervals: list[tuple[datetime, datetime]] = []
    in_win = False
    for i, ok in enumerate(below):
        if ok and not in_win:
            win_start = times[i]
            in_win = True
        elif not ok and in_win:
            intervals.append((win_start, times[i - 1]))
            in_win = False
    if in_win:
        intervals.append((win_start, times[-1]))
    return intervals


def __altaz_frame(loc: EarthLocation, t: Time):
    from astropy.coordinates import AltAz

    return AltAz(obstime=t, location=loc)


def target_alt_moon_sep(
    site: SiteInfo,
    ra_deg: float,
    dec_deg: float,
    times: Iterable[datetime],
) -> tuple[np.ndarray, np.ndarray]:
    """返回 (目标高度角 deg, 目标-月亮角距 deg)，与 times 等长。"""
    t = Time(list(times), format="datetime", scale="utc")
    loc = site.earth_location()
    frame = __altaz_frame(loc, t)
    target = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    altaz = target.transform_to(frame)
    moon_altaz = get_body("moon", t, loc).transform_to(frame)
    moon_sep = altaz.separation(moon_altaz).deg
    return altaz.alt.deg, moon_sep


@dataclass
class Feasibility:
    """单目标在某观测夜的可行性解释。"""

    target_id: int
    feasible: bool
    visible_intervals: list[tuple[datetime, datetime]]  # 同时满足高度+月距+黑夜
    min_altitude_deg: float
    min_moon_separation_deg: float
    max_altitude_deg: float
    min_moon_sep_observed_deg: float
    dark_minutes: float
    reasons: list[str]  # 不可执行原因（人类可读，供前端展示）


def assess_target(
    site: SiteInfo,
    target,  # app.models.Target
    start: datetime,
    end: datetime,
    dark_intervals: list[tuple[datetime, datetime]],
) -> Feasibility:
    """在 [start, end] 上评估目标：黑夜 ∩ 高度角 ∩ 月距。

    target 需含 ra_deg / dec_deg / min_altitude_deg / min_moon_separation_deg。
    """
    times = grid_times(start, end)
    alt, moon_sep = target_alt_moon_seg_safe(site, target, times)

    dark = np.zeros(len(times), dtype=bool)
    for a, b in dark_intervals:
        dark |= np.array([a <= tt <= b for tt in times])

    ok_alt = alt >= target.min_altitude_deg
    ok_moon = moon_sep >= target.min_moon_separation_deg
    ok = dark & ok_alt & ok_moon

    intervals = _mask_intervals(times, ok)
    reasons: list[str] = []
    if not dark.any():
        reasons.append("该夜没有天文晨昏黑夜窗口（太阳始终高于 -18°）")
    if not ok_alt.any():
        reasons.append(
            f"整夜高度角低于 {target.min_altitude_deg:.0f}°"
            f"（最高 {alt.max():.1f}°）"
        )
    elif dark.any() and not (dark & ok_alt).any():
        # 白天虽高，但黑夜时段不够高
        night_alt_max = alt[dark].max() if dark.any() else alt.max()
        reasons.append(
            f"黑夜窗口内高度角不足 {target.min_altitude_deg:.0f}°"
            f"（夜内最高 {night_alt_max:.1f}°）"
        )
    if not ok_moon.any():
        reasons.append(
            f"整夜月距不足 {target.min_moon_separation_deg:.0f}°"
            f"（最大月距 {moon_sep.max():.1f}°）"
        )
    elif dark.any() and not (dark & ok_moon).any():
        night_moon_max = moon_sep[dark].max() if dark.any() else moon_sep.max()
        reasons.append(
            f"黑夜窗口内月距不足 {target.min_moon_separation_deg:.0f}°"
            f"（夜内最大 {night_moon_max:.1f}°）"
        )
    if ok_alt.any() and ok_moon.any() and dark.any() and not ok.any():
        reasons.append("高度角与月距条件无法在同一黑夜时段同时满足")

    dark_minutes = sum(
        (b - a).total_seconds() for a, b in dark_intervals
    ) / 60.0
    return Feasibility(
        target_id=target.id,
        feasible=bool(ok.any()),
        visible_intervals=intervals,
        min_altitude_deg=target.min_altitude_deg,
        min_moon_separation_deg=target.min_moon_separation_deg,
        max_altitude_deg=float(alt.max()),
        min_moon_sep_observed_deg=float(moon_sep.min()),
        dark_minutes=round(dark_minutes, 1),
        reasons=reasons,
    )


def target_alt_moon_seg_safe(site: SiteInfo, target, times):
    """批量计算；长目标列表时按夜分批，避免一次性构造过大矩阵。"""
    return target_alt_moon_sep(site, target.ra_deg, target.dec_deg, times)


def _mask_intervals(
    times: list[datetime], mask: np.ndarray
) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    start_t = None
    for i, ok in enumerate(mask):
        if ok and start_t is None:
            start_t = times[i]
        elif not ok and start_t is not None:
            out.append((start_t, times[i - 1]))
            start_t = None
    if start_t is not None:
        out.append((start_t, times[-1]))
    return out
