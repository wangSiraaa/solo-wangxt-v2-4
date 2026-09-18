"""UTC 时间区间工具（datetime 均为带时区的 UTC）。"""
from __future__ import annotations

from datetime import datetime, timedelta

Interval = tuple[datetime, datetime]


def merge(intervals: list[Interval], gap: timedelta = timedelta(0)) -> list[Interval]:
    """合并重叠或间隔小于 gap 的区间。"""
    if not intervals:
        return []
    items = sorted((a, b) for a, b in intervals if b > a)
    out: list[Interval] = [items[0]]
    for a, b in items[1:]:
        pa, pb = out[-1]
        if a <= pb + gap:
            out[-1] = (pa, max(pb, b))
        else:
            out.append((a, b))
    return out


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if hi > lo:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def subtract(a: list[Interval], b: list[Interval]) -> list[Interval]:
    """从 a 中扣除 b。"""
    if not b:
        return list(a)
    out: list[Interval] = []
    j = 0
    for a0, a1 in a:
        cur = a0
        while j < len(b) and b[j][1] <= a0:
            j += 1
        k = j
        c = cur
        while k < len(b) and b[k][0] < a1:
            b0, b1 = b[k]
            if b0 > c:
                out.append((c, min(b0, a1)))
            c = max(c, b1)
            if c >= a1:
                break
            k += 1
        if c < a1:
            out.append((c, a1))
    return out


def clip(intervals: list[Interval], lo: datetime, hi: datetime) -> list[Interval]:
    return [(max(a, lo), min(b, hi)) for a, b in intervals if min(b, hi) > max(a, lo)]


def total_seconds(intervals: list[Interval]) -> float:
    return sum((b - a).total_seconds() for a, b in intervals)


def earliest_gap(
    windows: list[Interval],
    busy: list[Interval],
    duration: float,
    not_before: datetime,
    hard_end: datetime | None = None,
) -> Interval | None:
    """在 windows − busy 中找最早能容纳 duration 秒的空隙。"""
    free = intersect(windows, _invert(busy, windows))
    free = clip(free, not_before, hard_end or datetime.max.replace(tzinfo=windows[0][0].tzinfo))
    for a, b in free:
        if (b - a).total_seconds() + 1e-6 >= duration:
            return (a, a + timedelta(seconds=duration))
    return None


def _invert(busy: list[Interval], bounds: list[Interval]) -> list[Interval]:
    return subtract(bounds, merge(busy))
