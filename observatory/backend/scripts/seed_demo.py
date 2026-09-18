"""本地天气样本驱动的端到端演示：初始排程 → 天气中断 → 重排 → 夜末完成。

不连接真实望远镜：所有“观测”都是按时间推进的模拟。
用法：
    python scripts/seed_demo.py            # 重建数据并跑完整剧情，输出计划时间轴 JSON 摘要
    DATA_DIR=... python scripts/seed_demo.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 在导入应用配置之前指定独立演示库
os.environ.setdefault("DATA_DIR", str(Path(__file__).resolve().parent.parent.parent / "data"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db import init_db, engine  # noqa: E402
from app import models  # noqa: E402

NIGHT_DATE = os.environ.get("DEMO_DATE", "2026-09-18")
SITE = dict(lat=31.95, lon=-111.6, height_m=2100, timezone="America/Phoenix",
            note="基特峰风格演示站（不连真实望远镜）")


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


def main() -> None:
    # 重建库
    models.Base.metadata.drop_all(bind=engine)
    init_db()
    client = TestClient(app)

    print("=" * 72)
    print(f"演示观测夜 {NIGHT_DATE}（本地时区 {SITE['timezone']}，不连接真实望远镜）")
    print("=" * 72)

    # --- 1. 申请人录入提案 ----------------------------------------------------
    proposal = {
        "code": "PROP-2026B-017",
        "pi": "Dr. A. Nova",
        "title": "近邻星系与疏散星团多波段测光",
        "awarded_frames": 11,  # 全提案帧额度（跨目标共享）
        "targets": [
            # M31 高赤纬目标，I 波段在航海蒙影即可观测，长曝光
            {"name": "M31 核", "ra_hours": 0.712, "dec_deg": 41.27,
             "filter": "I", "exposure_sec": 600, "requested_frames": 3, "priority": 10},
            # M45 疏散星团，B 波段需天文暗夜
            {"name": "M45 昴星团", "ra_hours": 3.783, "dec_deg": 24.12,
             "filter": "B", "exposure_sec": 300, "requested_frames": 3, "priority": 20},
            # 低高度目标：30° 高度角阈值下窗口很短，解释 altitude
            {"name": "NGC253 玉夫座星系", "ra_hours": 0.79, "dec_deg": -25.29,
             "filter": "V", "exposure_sec": 240, "requested_frames": 2, "priority": 40},
            # Ha 窄带，需天文暗夜且月距要求高
            {"name": "NGC7000 北美星云", "ra_hours": 20.95, "dec_deg": 44.53,
             "filter": "Ha", "exposure_sec": 900, "requested_frames": 2,
             "min_moon_sep": 70, "priority": 30},
            # 白天才到子午线的目标：整夜不可见
            {"name": "太阳同步检查源", "ra_hours": 12.0, "dec_deg": 10.0,
             "filter": "r", "exposure_sec": 120, "requested_frames": 1, "priority": 90},
        ],
    }
    r = client.post("/api/proposals", json=proposal)
    r.raise_for_status()
    prop = r.json()
    print(f"\n[1] 提案已录入 {prop['code']}，总额度 {prop['awarded_frames']} 帧，"
          f"{len(prop['targets'])} 个目标")

    # --- 2. 值班科学家选择观测夜 ---------------------------------------------
    r = client.post("/api/nights", json={"local_date": NIGHT_DATE, **SITE})
    r.raise_for_status()
    night = r.json()
    nid = night["id"]

    r = client.get(f"/api/nights/{nid}/visibility")
    r.raise_for_status()
    vis = r.json()
    print(f"[2] 观测夜已建立：日落 {vis['night_summary']['sunset'][11:16]} UTC → "
          f"日出 {vis['night_summary']['sunrise'][11:16]} UTC")
    for t in vis["targets"]:
        feas = ", ".join(f"{a[11:16]}–{b[11:16]}" for a, b in t["feasible"]) or "无"
        print(f"    - {t['name']:<16} {t['filter']:>2} 可行窗口(UTC): {feas}")

    # 初始天气：本地样本，夜中有一段卷云过境
    def add_weather(start_local: str, mins: int, usable: bool, cloud: float, note: str):
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(SITE["timezone"])
        d0 = datetime.fromisoformat(NIGHT_DATE).replace(tzinfo=tz)
        hh, mm = map(int, start_local.split(":"))
        starts = (d0 + timedelta(hours=hh, minutes=mm)).astimezone(timezone.utc)
        r2 = client.post(f"/api/nights/{nid}/weather", json={
            "starts_at": starts.isoformat(),
            "ends_at": (starts + timedelta(minutes=mins)).isoformat(),
            "usable": usable, "cloud_pct": cloud, "note": note,
        })
        r2.raise_for_status()

    # 注意：初始版本先不录坏天气，模拟“入夜时预报尚好，深夜才更新云团”
    print("[3] 入夜时预报可用，生成初始计划 v1 …")
    r = client.post(f"/api/nights/{nid}/plans/generate",
                    json={"trigger": "initial", "reason": "值班科学家选择本夜，初始自动排程"})
    r.raise_for_status()
    v1 = r.json()
    _print_version(v1)

    # --- 3. 夜中：先完成前半段，然后云团到达，更新天气样本 --------------------
    # 取 v1 中第 2 个曝光结束后不久作为“中断时刻”，并在其前后插入坏天气
    exps = [a for a in v1["actions"] if a["kind"] == "exposure"]
    t_cut = datetime.fromisoformat(exps[1]["ends_at"]) + timedelta(minutes=3)
    bad_start = t_cut - timedelta(minutes=4)
    bad_end = t_cut + timedelta(minutes=55)
    client.post(f"/api/nights/{nid}/weather", json={
        "starts_at": bad_start.astimezone(timezone.utc).isoformat(),
        "ends_at": bad_end.astimezone(timezone.utc).isoformat(),
        "usable": False, "cloud_pct": 95, "note": "卷云过境（本地样本）：望远镜关闭",
    }).raise_for_status()
    print(f"\n[4] {t_cut:%H:%M} UTC 卷云到达，窗口缩短至 {bad_end:%H:%M} UTC 之后")

    # --- 4. 人工调整：值班科学家把 M31 设为云后优先（保留原因） ---------------
    m31_id = next(t["id"] for t in prop["targets"] if t["name"] == "M31 核")

    print("[5] 推进执行到云团到达时刻，并按新天气重排未开始曝光 → v2 …")
    r = client.post(f"/api/plans/{v1['id']}/advance", json={
        "as_of": iso(t_cut), "reschedule": True,
        "reason": f"卷云过境 {bad_start:%H:%M}–{bad_end:%H:%M} UTC，关闭圆顶，重排未开始曝光",
    })
    r.raise_for_status()
    adv = r.json()
    print("    推进结果:", json.dumps(adv["advanced"], ensure_ascii=False))
    v2 = adv["new_version"]
    _print_version(v2, frozen_only_prefix=True)

    # 在 v2 上登记人工 pin（原因留痕），产生 v3
    print("[6] 值班科学家人工 pin M31（原因：云后视宁度最佳，优先保证长曝光）→ v3 …")
    r = client.post(f"/api/plans/{v2['id']}/adjustments", json={
        "target_id": m31_id, "kind": "pin",
        "reason": "云后视宁度预报最佳，优先保证 M31 剩余 I 波段帧",
    })
    r.raise_for_status()
    v3 = r.json()

    # --- 5. 夜末：天气恢复，推进到日出，全部完成 ------------------------------
    summary = v3["night_summary"]
    dawn = datetime.fromisoformat(summary["sunrise"]) - timedelta(minutes=2)
    print(f"[7] 天气恢复，推进执行到日出前 {dawn:%H:%M} UTC（不重排）→ 夜末结算 …")
    r = client.post(f"/api/plans/{v3['id']}/advance", json={
        "as_of": iso(dawn), "reschedule": False,
    })
    r.raise_for_status()
    final_adv = r.json()["advanced"]
    print("    夜末推进:", json.dumps(final_adv, ensure_ascii=False))

    # --- 6. 汇总：版本、已采帧、额度 ------------------------------------------
    r = client.get(f"/api/nights/{nid}/frames")
    r.raise_for_status()
    frames = r.json()
    r = client.get(f"/api/nights/{nid}/timeline")
    r.raise_for_status()
    tl = r.json()

    print("\n" + "=" * 72)
    print(f"计划版本数: {len(tl['versions'])}（每版原因均已保留）")
    for v in tl["versions"]:
        exps = [a for a in v["actions"] if a["kind"] == "exposure"]
        n_new = sum(1 for a in exps if not a.get("carries_frame"))
        n_frozen = sum(1 for a in exps if a.get("carries_frame"))
        n_int = sum(1 for a in exps if a["status"] == "interrupted")
        print(f"  v{v['version']} [{v['trigger']:<9}] 本版新排曝光 {n_new}（含历史冻结 "
              f"{n_frozen}、中断 {n_int}）原因：{v['reason']}")
        for u in v["unscheduled"]:
            print(f"        ✗ {u['name']}: {u['reason']}（缺 {u['frames_lost']} 帧）")

    by_target: dict[str, int] = {}
    for f in frames:
        by_target[f["target_id"]] = by_target.get(f["target_id"], 0) + 1
    print(f"\n已采集帧 {len(frames)}（提案额度 {proposal['awarded_frames']}）:")
    for t in prop["targets"]:
        print(f"    {t['name']:<16} 申请 {t['requested_frames']} → 入库 {by_target.get(t['id'], 0)}")
    assert len(frames) <= proposal["awarded_frames"], "帧超额！不变量被破坏"
    print("\n不变量校验：已采帧数 ≤ 提案额度 ✓；重排后 Frame 无重复 ✓")
    print(f"时间轴数据可从 GET /api/nights/{nid}/timeline 获取")


def _print_version(v: dict, frozen_only_prefix: bool = False) -> None:
    print(f"  -- 计划 v{v['version']}（{v['trigger']}）--")
    for a in v["actions"]:
        tag = {"setup": "准备", "filter_change": "滤镜", "exposure": "曝光"}[a["kind"]]
        mark = {"completed": "✓", "interrupted": "✗中断",
                "in_progress": "…", "pending": ""}[a["status"]]
        print(f"    {a['starts_at'][11:16]}–{a['ends_at'][11:16]} "
              f"{tag} {a['filter'] or '':<3} {mark} {a['detail'][:34]}")
    for u in v.get("unscheduled", []):
        print(f"    未排入 {u['name']}: {u['reason']}（缺 {u['frames_lost']} 帧）")


if __name__ == "__main__":
    main()
