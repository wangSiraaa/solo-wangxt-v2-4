"""夜间计划调度器。

核心不变量：
* 只有曝光 *完成* 才创建 Frame；Frame 是占用申请额度的唯一记录。
* 重排（天气/人工）时，as_of 之前已完成的动作原样冻结到新版本；
  被重排时刻切断、尚未完成的动作标记 interrupted，帧丢失且不占额度，可重新排入。
* 滤镜切换与设备准备必须在“目标可行曝光段开始前”就位，且只允许发生在
  望远镜可工作（日落—日出且天气 usable）的时间；曝光本身还需满足
  高度角/月距/暗天光。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import astronomy, intervals as iv
from .config import (
    DEFAULT_MIN_ALTITUDE, DEFAULT_MOON_SEPARATION, FILTERS,
    GRID_STEP_SEC, READOUT_SECONDS, SETUP_TARGET_SECONDS,
)
from .models import (
    ActionKind, ActionStatus, Frame, ManualAdjustment, Night, PlanAction,
    PlanVersion, Proposal, Target, WeatherSample,
)

# 同目标相邻曝光（经读出后紧接下一帧、中间无其他目标）间隔 ≤ 此值时免重新找星
CONTIGUOUS_GAP = timedelta(minutes=15)


def _uid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Overrides:
    dropped: set[str] = field(default_factory=set)
    pinned: set[str] = field(default_factory=set)
    priority_boost: dict[str, int] = field(default_factory=dict)
    min_altitude: dict[str, float] = field(default_factory=dict)
    min_moon_sep: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_adjustments(cls, adjustments: list[ManualAdjustment]) -> "Overrides":
        o = cls()
        for adj in adjustments:
            p = adj.payload or {}
            if adj.kind == "drop":
                o.dropped.add(adj.target_id)
                o.pinned.discard(adj.target_id)
            elif adj.kind == "pin":
                o.dropped.discard(adj.target_id)
                o.pinned.add(adj.target_id)
            elif adj.kind == "reorder":
                if adj.target_id and p.get("priority_boost") is not None:
                    o.priority_boost[adj.target_id] = int(p["priority_boost"])
            elif adj.kind == "override_constraint":
                if p.get("min_altitude") is not None:
                    o.min_altitude[adj.target_id] = float(p["min_altitude"])
                if p.get("min_moon_sep") is not None:
                    o.min_moon_sep[adj.target_id] = float(p["min_moon_sep"])
        return o


def _weather_blocked(samples: list[WeatherSample], horizon) -> list[tuple[datetime, datetime]]:
    bad = [(s.starts_at, s.ends_at) for s in samples if not s.usable]
    if horizon:
        bad = iv.clip(bad, horizon[0], horizon[1])
    return iv.merge(bad)


def _frame_exists(db: Session, action_id: str) -> bool:
    return db.scalar(select(Frame.id).where(Frame.action_id == action_id)) is not None


def _acquired_counts(db: Session, night_id: str) -> dict[str, int]:
    """本夜已采集帧（历史版本中完成的曝光），按 target 计数。"""
    rows = db.execute(select(Frame.target_id).where(Frame.night_id == night_id)).all()
    counts: dict[str, int] = {}
    for (tid,) in rows:
        counts[tid] = counts.get(tid, 0) + 1
    return counts


def _proposal_usage(db: Session, night_id: str) -> dict[str, int]:
    rows = db.execute(
        select(Target.proposal_id)
        .join(Frame, Frame.target_id == Target.id)
        .where(Frame.night_id == night_id)
    ).all()
    out: dict[str, int] = {}
    for (pid,) in rows:
        out[pid] = out.get(pid, 0) + 1
    return out


def _freeze_prior_actions(db: Session, prev: PlanVersion | None, as_of: datetime):
    """返回（冻结动作字典、busy 区间、当前滤镜、最后曝光目标、最后曝光结束）。"""
    frozen: list[dict] = []
    busy: list[tuple[datetime, datetime]] = []
    last_filter: str | None = None
    last_exp_target: str | None = None
    last_exp_end: datetime | None = None

    if prev is None:
        return frozen, busy, last_filter, last_exp_target, last_exp_end

    for act in prev.actions:
        if act.ends_at <= as_of and act.status == ActionStatus.completed:
            start, end, status = act.starts_at, act.ends_at, ActionStatus.completed
            if act.kind == ActionKind.filter_change:
                last_filter = act.filter
            if act.kind == ActionKind.exposure:
                last_exp_target = act.target_id
                last_exp_end = end
        elif act.status == ActionStatus.interrupted and act.starts_at < as_of:
            # 已开始但未完成（曝光进行中或读出时被天气打断）：截断到 as_of
            start, end, status = act.starts_at, min(act.ends_at, as_of), ActionStatus.interrupted
        elif act.starts_at < as_of < act.ends_at:
            start, end, status = act.starts_at, as_of, ActionStatus.interrupted
        else:
            continue  # 未开始的动作丢弃，等待重排
        frozen.append({
            "target_id": act.target_id,
            "kind": act.kind,
            "filter": act.filter,
            "starts_at": start,
            "ends_at": end,
            "duration_sec": (end - start).total_seconds(),
            "status": status,
            "lineage_id": act.lineage_id,
            "carries_frame": (
                act.kind == ActionKind.exposure
                and status == ActionStatus.completed
                and not act.carries_frame
                and _frame_exists(db, act.id)
            ) or act.carries_frame,
            "detail": act.detail,
        })
        busy.append((start, end))
        if act.kind == ActionKind.exposure and status == ActionStatus.completed:
            # 已完成曝光后的读出仍占用望远镜
            busy.append((end, end + timedelta(seconds=READOUT_SECONDS)))
    return frozen, iv.merge(busy), last_filter, last_exp_target, last_exp_end


def _diagnose_target(tw, reason_no_window: str | None) -> str:
    if reason_no_window:
        return reason_no_window
    if not tw.alt_windows:
        return f"altitude: 整夜高度角未达到 {tw.min_alt:.0f}°"
    if not tw.moon_windows:
        return f"moon: 整夜与月球角距小于 {tw.min_sep:.0f}°"
    if not iv.intersect(tw.alt_windows, tw.moon_windows):
        return "altitude+moon: 高度角满足时月距不足（或反之）"
    if not tw.dark_windows:
        return "twilight: 该滤镜要求的暗天光在本地不存在（如夏夜高纬）"
    if not tw.feasible:
        return "twilight: 可见窗口与所需晨昏蒙影（暗天光等级）不相交"
    return "window_full: 可行窗口已被更高优先级目标或天气占用"


def build_plan(
    db: Session,
    night: Night,
    *,
    trigger: str,
    reason: str = "",
    as_of: datetime | None = None,
    overrides: Overrides | None = None,
    author: str = "scientist-on-duty",
    grid_step_sec: int | None = None,
) -> PlanVersion:
    """生成一个新的计划版本（不提交事务，由调用方 commit）。"""
    overrides = overrides or Overrides()
    step = grid_step_sec or GRID_STEP_SEC

    prev = db.scalar(
        select(PlanVersion).where(PlanVersion.night_id == night.id)
        .order_by(PlanVersion.version.desc()).limit(1)
    )
    if as_of is None:
        if prev is not None:
            # 人工重排沿用上一版本执行推进到的时刻，把已完成/中断动作正确冻结
            as_of = max(
                (a.ends_at for a in prev.actions
                 if a.status in (ActionStatus.completed, ActionStatus.interrupted)),
                default=utcnow(),
            )
        else:
            as_of = utcnow()

    location = astronomy.make_location(night.lat, night.lon, night.height_m)
    nw = astronomy.night_windows(night.local_date, location, night.timezone)
    horizon = nw.horizon
    if horizon is None:
        raise ValueError("该观测夜没有日出/日落（极昼/极夜），无法排程")

    bad_weather = _weather_blocked(night.weather_samples, horizon)
    work_windows = iv.subtract([horizon], bad_weather)  # 设备可工作时间

    frozen, busy, current_filter, _, _ = _freeze_prior_actions(db, prev, as_of)
    acquired = _acquired_counts(db, night.id)
    proposal_used = _proposal_usage(db, night.id)

    proposals = {p.id: p for p in db.scalars(select(Proposal)).all()}
    targets = list(db.scalars(select(Target).order_by(Target.priority, Target.created_at)).all())
    targets = [t for t in targets if t.filter in FILTERS]

    windows: dict[str, astronomy.TargetWindows] = {}
    for t in targets:
        min_alt = overrides.min_altitude.get(t.id, t.min_altitude or DEFAULT_MIN_ALTITUDE)
        min_sep = overrides.min_moon_sep.get(t.id, t.min_moon_sep or DEFAULT_MOON_SEPARATION)
        dark = nw.dark_for_level(FILTERS[t.filter]["dark_level"])
        windows[t.id] = astronomy.target_windows(
            t.ra_deg, t.dec_deg, location, horizon, dark,
            min_alt, min_sep, grid_step_sec=step,
        )

    def sort_key(t: Target):
        return (
            0 if t.id in overrides.pinned else 1,
            t.priority - overrides.priority_boost.get(t.id, 0),
            t.created_at, t.id,
        )

    ordered = sorted(targets, key=sort_key)
    new_actions: list[PlanAction] = []
    unscheduled: list[dict] = []
    # 各目标本版本内最后一帧曝光结束时间：只有真正背靠背才免重新准备
    last_end_by_target: dict[str, datetime] = {}
    # 提案剩余可排帧（含本版本尚未采集的预占），每排一帧都递减
    prop_left: dict[str, int] = {
        pid: max(0, p.awarded_frames - proposal_used.get(pid, 0))
        for pid, p in proposals.items()
    }

    def add_action(kind, target_id, flt, start: datetime, dur: float, detail=""):
        return PlanAction(
            target_id=target_id, kind=kind, filter=flt,
            starts_at=start, ends_at=start + timedelta(seconds=dur),
            duration_sec=dur, status=ActionStatus.pending,
            lineage_id=_uid(), detail=detail,
        )

    for t in ordered:
        if t.id in overrides.dropped:
            unscheduled.append({"target_id": t.id, "name": t.name, "frames_lost": 0,
                                "reason": "manual_drop: 值班科学家人工移出本夜计划"})
            continue

        done = acquired.get(t.id, 0)
        wanted = t.requested_frames - done
        if wanted <= 0:
            continue

        if prop_left[t.proposal_id] <= 0:
            unscheduled.append({
                "target_id": t.id, "name": t.name, "frames_lost": wanted,
                "reason": "award_exhausted: 提案帧总额度已被已采集/已预占帧用完",
            })
            continue

        # 每帧放置前都重新对照提案剩余额度（可能被前面的目标耗尽）
        window_failed = False
        while wanted > 0 and prop_left[t.proposal_id] > 0:
            block = _place_frame(
                tw=windows[t.id], target=t, work_windows=work_windows, busy=busy,
                current_filter=current_filter,
                last_end=last_end_by_target.get(t.id),
                not_before=as_of, horizon=horizon, step_sec=step,
            )
            if block is None:
                weather_reason = None
                if windows[t.id].feasible and not iv.subtract(windows[t.id].feasible, bad_weather):
                    weather_reason = "weather: 可见窗口完全落在天气不可用时段"
                unscheduled.append({
                    "target_id": t.id, "name": t.name, "frames_lost": wanted,
                    "reason": _diagnose_target(windows[t.id], weather_reason),
                })
                window_failed = True
                break

            if block.filter_dur > 0:
                fa = add_action(
                    ActionKind.filter_change, t.id, t.filter,
                    block.prep_start, block.filter_dur,
                    f"切换到 {t.filter}（{FILTERS[t.filter]['label']}）")
                new_actions.append(fa)
                busy.append((fa.starts_at, fa.ends_at))
            if block.setup_dur > 0:
                setup_begin = block.prep_start + timedelta(seconds=block.filter_dur)
                sa = add_action(
                    ActionKind.setup, t.id, t.filter, setup_begin, block.setup_dur,
                    "设备准备：定位/调焦/导星校准")
                new_actions.append(sa)
                busy.append((sa.starts_at, sa.ends_at))

            exp = add_action(
                ActionKind.exposure, t.id, t.filter, block.exp_start,
                t.exposure_sec,
                f"{t.filter} {t.exposure_sec:.0f}s 曝光")
            new_actions.append(exp)
            # 曝光 + 读出均占用望远镜（读出不是动作，但挡住后续放置）
            busy.append((exp.starts_at, exp.ends_at + timedelta(seconds=READOUT_SECONDS)))
            current_filter = t.filter
            last_end_by_target[t.id] = exp.ends_at + timedelta(seconds=READOUT_SECONDS)
            wanted -= 1
            prop_left[t.proposal_id] -= 1  # 预占提案帧额度

        if wanted > 0 and prop_left[t.proposal_id] <= 0 and not window_failed:
            unscheduled.append({
                "target_id": t.id, "name": t.name, "frames_lost": wanted,
                "reason": "award_exhausted: 提案帧总额度被更高优先级目标占满",
            })

    version_no = (prev.version + 1) if prev else 1
    pv = PlanVersion(
        night_id=night.id, version=version_no, trigger=trigger, reason=reason,
        created_at=utcnow(), unscheduled=unscheduled, night_summary=nw.summary,
    )
    db.add(pv)
    db.flush()

    out_seq = 0
    for f in frozen:
        out_seq += 1
        db.add(PlanAction(plan_version_id=pv.id, sequence=out_seq, **f))
    for act in sorted(new_actions, key=lambda a: (a.starts_at, a.lineage_id)):
        out_seq += 1
        act.sequence = out_seq
        act.plan_version_id = pv.id
        db.add(act)

    db.flush()
    return pv


@dataclass
class _PlacedBlock:
    prep_start: datetime
    setup_dur: float
    filter_dur: float
    exp_start: datetime
    exp_dur: float


def _place_frame(*, tw, target: Target, work_windows, busy, current_filter,
                 last_end, not_before, horizon, step_sec: int):
    """为单帧找最早可放置位置（含必要前置准备）；放不下返回 None。

    连续性判定：仅当该目标本版本最后一帧在本帧候选起点前 ≤15 分钟结束、
    且当前滤镜就是本目标滤镜时，才省略滤镜切换与找星。
    返回 _PlacedBlock(prep_start, setup_dur, filter_dur, exp_start, exp_dur)。
    """
    exp_dur = target.exposure_sec
    filter_dur = FILTERS[target.filter]["switch_seconds"] if current_filter != target.filter else 0.0
    setup_dur = SETUP_TARGET_SECONDS

    # 先找“无需前置准备”的最早曝光起点，用于判断是否与本目标上一帧背靠背
    bare_start = _earliest_start(
        exp_windows=tw.feasible, work_windows=work_windows, busy=busy,
        exp_dur=exp_dur, readout_sec=READOUT_SECONDS, prep_dur=0.0,
        not_before=not_before, hard_end=horizon[1], step_sec=step_sec,
    )
    if (last_end is not None and current_filter == target.filter
            and bare_start is not None
            and timedelta(0) <= bare_start - last_end <= CONTIGUOUS_GAP):
        return _PlacedBlock(bare_start, 0.0, 0.0, bare_start, exp_dur)

    # 否则找包含滤镜切换 + 设备准备的起点
    prep_dur = setup_dur + filter_dur
    exp_start = _earliest_start(
        exp_windows=tw.feasible, work_windows=work_windows, busy=busy,
        exp_dur=exp_dur, readout_sec=READOUT_SECONDS, prep_dur=prep_dur,
        not_before=not_before, hard_end=horizon[1], step_sec=step_sec,
    )
    if exp_start is None:
        return None

    prep_start = exp_start - timedelta(seconds=prep_dur)
    return _PlacedBlock(prep_start, setup_dur, filter_dur, exp_start, exp_dur)


def _earliest_start(*, exp_windows, work_windows, busy, exp_dur, readout_sec: float,
                    prep_dur, not_before, hard_end, step_sec: int) -> datetime | None:
    """最早曝光起点 s，满足：
      [s, s+exp_dur+readout] ⊂ exp_windows ∩ work_windows，且不撞 busy；
      [s-prep_dur, s] ⊂ work_windows，且不撞 busy（前置准备可在蒙影/非可见时段）。

    候选起点 = 网格点 ∪ 每个忙段末端 ∪ 每个忙段末端 + 读出（承接上一帧），
    因此 20s 读出后可立即开拍而无需对齐 60s 网格。
    """
    busy_m = iv.merge(busy)
    occupy = exp_dur + readout_sec
    usable = iv.merge(iv.intersect(exp_windows, work_windows))
    prep_free = iv.subtract(work_windows, busy_m)
    step = timedelta(seconds=step_sec)
    eps = timedelta(microseconds=1)
    readout = timedelta(seconds=readout_sec)

    def fits(s: datetime) -> bool:
        if s + timedelta(seconds=occupy) > hard_end + eps:
            return False
        if _collides((s, s + timedelta(seconds=occupy)), busy_m):
            return False
        if prep_dur <= 0:
            return True
        p0 = s - timedelta(seconds=prep_dur)
        return any(a <= p0 + eps and s <= b + eps for a, b in prep_free)

    for c0, c1 in usable:
        c0 = max(c0, not_before)
        # 该窗口内的候选点
        candidates = [c0]
        grid = c0
        while grid <= c1:
            candidates.append(grid)
            grid += step
        for a, b in busy_m:
            if c0 - step <= b <= c1 + step:
                candidates.append(b)          # 忙段刚结束
                candidates.append(b + readout)  # 忙段结束并完成读出
        for s in sorted(set(candidates)):
            s = max(s, c0)
            if s + timedelta(seconds=occupy) <= c1 + eps and fits(s):
                return s
    return None


def _collides(span, intervals) -> bool:
    a, b = span
    eps = timedelta(microseconds=1)
    for c, d in intervals:
        if a < d - eps and c < b - eps:
            return True
    return False
