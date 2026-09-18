"""本地天气样本演示（不连接真实望远镜）。

确定性地演绎一次完整夜班：
  v1 初始计划  -> 部分帧执行完成
  v2 天气样本缩短窗口：中断跨点曝光，只重排未开始曝光
  v3 值班科学家人工锁定一帧（带原因）
  v4 夜结束，全部完成帧入账

用法: .venv/bin/python -m app.demo
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import service
from .database import SessionLocal
from .models import (
    AcquiredFrame,
    Night,
    Override,
    PlanBlock,
    Target,
    WeatherInterval,
)
from .seed import NIGHT_DATE, reset_and_seed

# ---- 模拟时钟（UTC）。兴隆黑夜 11:48–20:20 UTC（北京 19:48–04:20）----
T_PRE_WEATHER = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
CLOUD_START = datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc)
CLOUD_END = datetime(2026, 9, 18, 15, 20, tzinfo=timezone.utc)
PIN_START = datetime(2026, 9, 18, 16, 0, tzinfo=timezone.utc)
PIN_END = datetime(2026, 9, 18, 16, 6, tzinfo=timezone.utc)
NIGHT_END = datetime(2026, 9, 18, 20, 20, tzinfo=timezone.utc)

TARGET_ORDER = ["M31", "M57", "M45", "omega Cen", "Sgr-候选 J1732-1200", "NGC 891"]


def _name(db, tid: int | None) -> str:
    if tid is None:
        return "—"
    return db.get(Target, tid).name


def print_version(db, ver) -> None:
    print(f"\n=== 计划 v{ver.version}（{ver.trigger}） {ver.reason} ===")
    n_done = sum(1 for b in ver.blocks if b.status == "completed")
    n_int = sum(1 for b in ver.blocks if b.status == "interrupted")
    print(f"块数 {len(ver.blocks)}：完成 {n_done}，中断 {n_int}")
    for b in ver.blocks:
        tag = {
            "setup": "设备准备",
            "filter_change": f"滤镜切换->{b.filter_name}",
            "science": f"曝光 #{b.frame_seq}",
        }[b.kind]
        flag = {
            "completed": "✓完成",
            "interrupted": "✗中断",
            "planned": "计划",
        }[b.status]
        note = f"  [{b.note}]" if b.note else ""
        print(
            f"  {b.start_utc:%H:%M}-{b.end_utc:%H:%M} UTC "
            f"{_name(db, b.target_id):>20} {tag:<14} {flag}{note}"
        )
    print("  -- 可行性解释 --")
    for name in TARGET_ORDER:
        for tid, f in ver.feasibility.items():
            t = db.get(Target, int(tid))
            if t.name != name:
                continue
            head = (
                f"  {t.name:<20} 可行={f['feasible']} "
                f"申请 {f['requested_frames']} / 已采 {f['acquired_frames']} "
                f"/ 本版新排 {f['scheduled_new_frames']}"
            )
            print(head)
            for r in f["reasons"]:
                print(f"      · {r}")


def main() -> None:
    refs = reset_and_seed()
    db = SessionLocal()
    try:
        night = db.get(Night, refs["night_id"])

        print(f"观测夜：{NIGHT_DATE} 兴隆站（北京时间），黑夜 19:48–04:20")
        # v1
        v1 = service.create_version(
            db, night, trigger="initial", reason="值班科学家生成初始夜间计划"
        )
        print_version(db, v1)

        # 14:00 前的帧执行完成
        frames = service.complete_until(db, night, v1, T_PRE_WEATHER)
        print(
            f"\n>> 模拟执行到 {T_PRE_WEATHER:%H:%M} UTC："
            f"已采集 {len(frames)} 帧 "
            f"{[(_name(db, f.target_id), f.frame_seq) for f in frames]}"
        )

        # 天气样本：14:00–15:20 云
        db.add(
            WeatherInterval(
                night_id=night.id,
                start_utc=CLOUD_START,
                end_utc=CLOUD_END,
                kind="cloud",
                source="sample",
                note="本地天气样本：中层云覆盖，无法观测",
            )
        )
        db.flush()
        interrupted = service.interrupt_planned_at(db, v1, CLOUD_START)
        print(f">> 天气样本 {CLOUD_START:%H:%M}-{CLOUD_END:%H:%M}：中断块 {len(interrupted)}")
        v2 = service.create_version(
            db,
            night,
            trigger="weather",
            reason=(
                "云窗缩短观测窗口：14:00 前完成帧保留；跨点曝光中断不计额度；"
                "仅重排未开始曝光"
            ),
            created_by="weather-sample",
            as_of=CLOUD_START,
        )
        print_version(db, v2)

        # 人工 pin：把 M45 第 3 帧锁定到 16:00-16:06（与 V 波段测光标定同步）
        m45 = next(t for t in refs["targets"].items() if t[0] == "M45")
        db.add(
            Override(
                night_id=night.id,
                version_id=v2.id,
                target_id=m45[1],
                action="pin",
                payload={
                    "start": PIN_START.isoformat(),
                    "end": PIN_END.isoformat(),
                    "frame_seq": 3,
                },
                reason="需与 16:00 UTC 的 V 波段标准星标定同步，值班科学家锁定",
                created_by="duty-scientist-高",
            )
        )
        db.flush()
        v3 = service.create_version(
            db,
            night,
            trigger="manual",
            reason="人工锁定 M45 #3 到标定同步时段（保留原因与版本）",
            created_by="duty-scientist-高",
            as_of=CLOUD_END,
        )
        print_version(db, v3)

        # 夜结束：登记剩余完成帧
        frames2 = service.complete_until(db, night, v3, NIGHT_END)
        night.status = "completed"
        db.commit()
        total = (
            db.query(AcquiredFrame).filter(AcquiredFrame.night_id == night.id).count()
        )
        print(f"\n>> 夜结束：本阶段登记 {len(frames2)} 帧，整夜共采集 {total} 帧")
        print(">> 已采集帧台账（不会因任何重排重复计数）：")
        for f in db.query(AcquiredFrame).order_by(AcquiredFrame.acquired_at):
            print(
                f"   {f.acquired_at:%H:%M} UTC {_name(db, f.target_id):>20} "
                f"#{f.frame_seq} {f.filter_name} {f.exposure_seconds}s"
            )
        print(">> 版本链：", [(v.version, v.trigger) for v in night.versions])
    finally:
        db.close()


if __name__ == "__main__":
    main()
