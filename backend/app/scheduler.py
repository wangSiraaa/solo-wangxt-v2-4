"""夜间排程引擎。

时间轴按 astronomy.GRID_MINUTES 网格化，布尔掩码表示：
  free   = 天文黑夜 ∩ 无天气 ∩ 无人工 hold ∩ 无人工 pin
  feas_t = free ∩ 目标高度角达标 ∩ 月距达标（科学曝光必须落在此处）

贪心策略（按时间向前）：在当前时刻，为每个还有剩余额度的目标计算
"下一帧最早能开始的时刻"（含设备准备 10min、滤镜切换 3min），
选择最早者（优先级其次）。准备与切换只要求 free，不要求目标高度角
（可以在目标升起过程中先寻星）。

重排时已完成/中断的块原样带到新版本，排程只动 as_of 之后、未开始的曝光；
已采集帧从 requested_frames 中扣减，不会重复占用申请额度。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from . import astronomy
from .config import (
    DEFAULT_FILTER_CHANGE_MINUTES,
    DEFAULT_READOUT_MINUTES,
    DEFAULT_SETUP_MINUTES,
    GRID_MINUTES,
)

SETUP_MIN = DEFAULT_SETUP_MINUTES
FILTER_CHANGE_MIN = DEFAULT_FILTER_CHANGE_MINUTES


@dataclass
class ScheduledBlock:
    kind: str  # setup | filter_change | science
    target_id: Optional[int]
    filter_name: Optional[str]
    start: datetime
    end: datetime
    frame_seq: Optional[int] = None
    status: str = "planned"  # planned | completed | interrupted
    note: str = ""


@dataclass
class Pin:
    target_id: int
    start: datetime
    end: datetime
    frame_seq: int


@dataclass
class PlanContext:
    start: datetime                       # 夜覆盖区间起（UTC）
    end: datetime                         # 止
    dark_intervals: list[tuple[datetime, datetime]]
    weather_intervals: list[tuple[datetime, datetime]]
    targets: list                         # app.models.Target 列表
    feas_masks: dict                     # target_id -> np.ndarray(bool)
    sky_reasons: dict                    # target_id -> [高度角/月距/晨昏不可行原因]
    acquired: dict[int, int]             # target_id -> 已采集帧数
    as_of: datetime                      # 重排起点（新版本从此时刻向后排）
    carried: list[ScheduledBlock] = field(default_factory=list)  # 已完成/中断块
    pins: list[Pin] = field(default_factory=list)
    holds: list[tuple[datetime, datetime]] = field(default_factory=list)
    setup_minutes: int = SETUP_MIN
    filter_change_minutes: int = FILTER_CHANGE_MIN


@dataclass
class TargetExplanation:
    target_id: int
    feasible: bool
    sky_reasons: list[str]
    requested: int
    acquired: int
    scheduled_new: int = 0
    total_in_plan: int = 0
    schedule_reason: str = ""

    def to_dict(self, visible_intervals) -> dict:
        return {
            "target_id": self.target_id,
            "feasible": self.feasible,
            "reasons": list(self.sky_reasons)
            + ([self.schedule_reason] if self.schedule_reason else []),
            "requested_frames": self.requested,
            "acquired_frames": self.acquired,
            "scheduled_new_frames": self.scheduled_new,
            "total_in_plan_frames": self.total_in_plan,
            "visible_intervals": [
                [a.isoformat(), b.isoformat()] for a, b in visible_intervals
            ],
        }


@dataclass
class PlanResult:
    blocks: list[ScheduledBlock]
    explanations: dict[int, TargetExplanation]
    visible: dict[int, list[tuple[datetime, datetime]]]
    summary: dict


def _grid_steps(seconds: float) -> int:
    return max(1, int(np.ceil(seconds / (GRID_MINUTES * 60))))


def _interval_mask(times: list[datetime], intervals) -> np.ndarray:
    m = np.zeros(len(times), dtype=bool)
    for a, b in intervals:
        m |= np.array([a <= t <= b for t in times])
    return m


def _earliest_run(mask: np.ndarray, from_i: int, length: int) -> Optional[int]:
    """mask[from_i:] 中最早连续 length 个 True 的起点索引。"""
    if length <= 0:
        return from_i
    n = mask.shape[0]
    i = from_i
    # 滑动连续计数，避免对每个起点切片
    run = 0
    while i < n:
        if mask[i]:
            run += 1
            if run >= length:
                return i - length + 1
        else:
            run = 0
        i += 1
    return None


def build_plan(ctx: PlanContext) -> PlanResult:
    times = astronomy.grid_times(ctx.start, ctx.end)
    idx_of = {t: i for i, t in enumerate(times)}
    n = len(times)

    dark = _interval_mask(times, ctx.dark_intervals)
    weather = _interval_mask(times, ctx.weather_intervals)
    held = _interval_mask(times, ctx.holds)

    free = dark & ~weather & ~held

    # 人工 pin：占用时间轴，并直接生成块
    pinned_blocks: list[ScheduledBlock] = []
    pinned_targets: dict[int, list[Pin]] = {t.id: [] for t in ctx.targets}
    for pin in ctx.pins:
        i0, i1 = idx_of[pin.start], idx_of[pin.end]
        free[i0:i1] = False  # 半开区间 [start, end)
        target = next(t for t in ctx.targets if t.id == pin.target_id)
        pinned_blocks.append(
            ScheduledBlock(
                kind="science",
                target_id=target.id,
                filter_name=target.filter_name,
                start=pin.start,
                end=pin.end,
                frame_seq=pin.frame_seq,
                note="人工锁定（pin）",
            )
        )
        pinned_targets[target.id].append(pin)

    # 带入新版本的历史块（completed / interrupted），同样占用 as_of 之前的时间
    blocks: list[ScheduledBlock] = list(ctx.carried)
    setup_done: set[int] = set()
    current_filter: Optional[str] = None
    for b in sorted(ctx.carried, key=lambda x: x.start):
        free[idx_of[b.start] : idx_of[b.end]] = False
        if b.kind == "setup" and b.status == "completed":
            setup_done.add(b.target_id)
        if b.kind in ("science", "filter_change") and b.target_id is not None:
            # 中断的 science 其滤镜也已在光路中
            current_filter = b.filter_name

    start_i = idx_of[min(times, key=lambda t: abs((t - ctx.as_of).total_seconds()))]
    start_i = max(start_i, 0)

    targets_by_id = {t.id: t for t in ctx.targets}
    remaining: dict[int, int] = {}
    pinned_seqs: dict[int, set[int]] = {}
    for t in ctx.targets:
        acq = ctx.acquired.get(t.id, 0)
        seqs = {p.frame_seq for p in pinned_targets.get(t.id, [])}
        pinned_seqs[t.id] = seqs
        pinned_n = len(seqs)
        remaining[t.id] = max(0, t.requested_frames - acq - pinned_n)

    explanations = {
        t.id: TargetExplanation(
            target_id=t.id,
            feasible=bool(ctx.feas_masks[t.id].any()),
            sky_reasons=list(ctx.sky_reasons.get(t.id, [])),
            requested=t.requested_frames,
            acquired=ctx.acquired.get(t.id, 0),
        )
        for t in ctx.targets
    }
    visible = {
        t.id: astronomy._mask_intervals(times, ctx.feas_masks[t.id])
        for t in ctx.targets
    }

    cursor = start_i
    new_blocks: list[ScheduledBlock] = []

    def chain_for(target) -> Optional[tuple[int, list[ScheduledBlock]]]:
        """该目标下一帧（含准备/切换）最早能完成排布的位置。"""
        chain: list[ScheduledBlock] = []
        i = cursor
        if target.id not in setup_done:
            k = _grid_steps(ctx.setup_minutes * 60)
            i = _earliest_run(free, i, k)
            if i is None:
                return None
            chain.append(
                ScheduledBlock(
                    kind="setup",
                    target_id=target.id,
                    filter_name=None,
                    start=times[i],
                    end=times[i + k],
                )
            )
            i = i + k
        if current_filter != target.filter_name:
            k = _grid_steps(ctx.filter_change_minutes * 60)
            i = _earliest_run(free, i, k)
            if i is None:
                return None
            chain.append(
                ScheduledBlock(
                    kind="filter_change",
                    target_id=target.id,
                    filter_name=target.filter_name,
                    start=times[i],
                    end=times[i + k],
                )
            )
            i = i + k
        k = _grid_steps(target.exposure_seconds + target.readout_seconds)
        science_mask = free & ctx.feas_masks[target.id]
        i = _earliest_run(science_mask, i, k)
        if i is None:
            return None
        # 帧号：已采集帧之后，跳过人工 pin 占用的帧号
        used = pinned_seqs[target.id] | {
            b.frame_seq
            for b in new_blocks
            if b.target_id == target.id and b.kind == "science"
        }
        seq = ctx.acquired.get(target.id, 0) + 1
        while seq in used:
            seq += 1
        chain.append(
            ScheduledBlock(
                kind="science",
                target_id=target.id,
                filter_name=target.filter_name,
                start=times[i],
                end=times[i + k],
                frame_seq=seq,
            )
        )
        return i, chain

    while True:
        best: Optional[tuple[int, int, list[ScheduledBlock], object]] = None
        # (science_start_idx, priority, chain, target)
        for target in sorted(ctx.targets, key=lambda t: (t.priority, t.id)):
            if remaining[target.id] <= 0:
                continue
            found = chain_for(target)
            if found is None:
                continue
            sci_i, chain = found
            if best is None or sci_i < best[0]:
                best = (sci_i, target.priority, chain, target)
        if best is None:
            break
        sci_i, _, chain, target = best
        # 占用链上所有块（半开区间 [start, end)）
        for b in chain:
            a, z = idx_of[b.start], idx_of[b.end]
            free[a:z] = False
            new_blocks.append(b)
            if b.kind == "setup":
                setup_done.add(target.id)
            if b.kind == "filter_change":
                current_filter = b.filter_name
            if b.kind == "science":
                current_filter = b.filter_name
        remaining[target.id] -= 1
        cursor = idx_of[chain[-1].end] + 1
        if cursor >= n:
            break

    blocks.extend(pinned_blocks)
    blocks.extend(new_blocks)
    blocks.sort(key=lambda b: b.start)

    # 解释：排了多少帧 / 为什么没排满
    for t in ctx.targets:
        new_sci = len(
            [b for b in new_blocks if b.target_id == t.id and b.kind == "science"]
        ) + len(pinned_targets.get(t.id, []))
        acq = ctx.acquired.get(t.id, 0)
        exp = explanations[t.id]
        exp.scheduled_new = new_sci
        exp.total_in_plan = acq + new_sci
        if not exp.feasible:
            exp.schedule_reason = ""
        elif new_sci < t.requested_frames - acq:
            need_min = (t.requested_frames - acq) * (
                t.exposure_seconds + t.readout_seconds
            ) / 60
            exp.schedule_reason = (
                f"剩余黑夜/天气窗口内仅能排入 {new_sci}/"
                f"{t.requested_frames - acq} 帧（全部完成约需 {need_min:.0f} 分钟，"
                f"含每目标 {ctx.setup_minutes} 分钟准备与滤镜切换）"
            )

    summary = _summary(ctx, blocks, new_blocks, pinned_blocks, explanations)
    return PlanResult(
        blocks=blocks,
        explanations=explanations,
        visible=visible,
        summary=summary,
    )


def _summary(ctx, blocks, new_blocks, pinned_blocks, explanations) -> dict:
    science = [b for b in blocks if b.kind == "science"]
    completed = [b for b in blocks if b.status == "completed"]
    interrupted = [b for b in blocks if b.status == "interrupted"]
    unscheduled = [
        t.id
        for t in ctx.targets
        if explanations[t.id].total_in_plan < t.requested_frames
    ]
    infeasible = [
        t.id for t in ctx.targets if not explanations[t.id].feasible
    ]
    return {
        "science_blocks": len(science),
        "completed_blocks": len(completed),
        "interrupted_blocks": len(interrupted),
        "new_science_blocks": len(
            [b for b in new_blocks if b.kind == "science"]
        )
        + len(pinned_blocks),
        "targets_unsaturated": unscheduled,
        "targets_infeasible": infeasible,
    }
