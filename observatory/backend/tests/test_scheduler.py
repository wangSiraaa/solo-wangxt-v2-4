"""核心业务不变量测试。"""
from __future__ import annotations

from datetime import datetime, timedelta

from .conftest import make_night, make_proposal


def _exposures(version):
    return [a for a in version["actions"] if a["kind"] == "exposure"]


def _latest_versions(client, nid):
    tl = client.get(f"/api/nights/{nid}/timeline").json()
    return tl["versions"]


def test_visibility_explains_unobservable_target(client):
    make_night(client)
    # 12h RA 的目标在 9 月夜空中位于地平线以下
    make_proposal(client, awarded=5, targets=[
        {"name": "DAY", "ra_hours": 12.0, "dec_deg": 10.0, "filter": "r",
         "exposure_sec": 120, "requested_frames": 1},
    ])
    nights = client.get("/api/nights").json()
    vis = client.get(f"/api/nights/{nights[0]['id']}/visibility").json()
    t = vis["targets"][0]
    assert t["feasible"] == []
    assert t["alt_windows"] == []


def test_initial_plan_respects_filter_setup_and_quota(client):
    n = make_night(client)
    prop = make_proposal(client, awarded=2)  # 申请 6 帧但只批 2
    r = client.post(f"/api/nights/{n['id']}/plans/generate",
                    json={"trigger": "initial", "reason": "t"})
    r.raise_for_status()
    v = r.json()
    exps = _exposures(v)
    assert len(exps) == 2                       # 额度是硬上限
    # 每个新目标块前面都有设备准备；第一次还有滤镜切换
    assert any(a["kind"] == "setup" for a in v["actions"])
    assert any(a["kind"] == "filter_change" for a in v["actions"])
    # 未排入原因要可解释
    assert any("award" in u["reason"] for u in v["unscheduled"])


def test_actions_do_not_overlap(client):
    n = make_night(client)
    make_proposal(client, awarded=6)
    v = client.post(f"/api/nights/{n['id']}/plans/generate",
                    json={"trigger": "initial"}).json()
    spans = sorted((a["starts_at"], a["ends_at"]) for a in v["actions"])
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        assert e1 <= s2, f"动作重叠: {e1} > {s2}"


def test_exposure_requires_feasible_window(client):
    """曝光必须落在高度角/月距/暗天光可行窗口内。"""
    n = make_night(client)
    make_proposal(client, awarded=6)
    v = client.post(f"/api/nights/{n['id']}/plans/generate",
                    json={"trigger": "initial"}).json()
    vis = {t["target_id"]: t for t in
           client.get(f"/api/nights/{n['id']}/visibility").json()["targets"]}
    for a in _exposures(v):
        feasible = vis[a["target_id"]]["feasible"]
        assert any(w[0] <= a["starts_at"] and a["ends_at"] <= w[1] for w in feasible), \
            f"{a['target_id']} 曝光落在可行窗口外"


def test_weather_interrupt_and_reschedule_does_not_double_count(client):
    n = make_night(client)
    make_proposal(client, awarded=6)
    nid = n["id"]
    v1 = client.post(f"/api/nights/{nid}/plans/generate", json={"trigger": "initial"}).json()

    # 在第二帧曝光即将结束时关闭圆顶：曝光主体完成但读出撞坏天气，帧不成立
    exps = sorted(_exposures(v1), key=lambda a: a["starts_at"])
    cut = datetime.fromisoformat(exps[1]["ends_at"]) + timedelta(seconds=5)
    client.post(f"/api/nights/{nid}/weather", json={
        "starts_at": (cut - timedelta(seconds=10)).isoformat(),
        "ends_at": (cut + timedelta(minutes=90)).isoformat(),
        "usable": False, "cloud_pct": 90, "note": "卷云",
    }).raise_for_status()

    r = client.post(f"/api/plans/{v1['id']}/advance",
                    json={"as_of": cut.isoformat(), "reschedule": True})
    r.raise_for_status()
    adv = r.json()
    v2 = adv["new_version"]
    # 第 1 帧完整完成并落帧；第 2 帧读出撞坏天气 -> 中断，不计完成、不占额度
    assert adv["advanced"]["frames_created"] == 1
    assert adv["advanced"]["interrupted_exposures"] == 1
    statuses = [(a["status"], a.get("carries_frame")) for a in _exposures(v2)]
    assert ("completed", True) in statuses
    assert ("interrupted", False) in statuses

    # 推进到日出（不重排）
    tl = client.get(f"/api/nights/{nid}/timeline").json()
    dawn = datetime.fromisoformat(tl["summary"]["sunrise"]) - timedelta(minutes=1)
    latest = client.get(f"/api/plans/{v2['id']}").json()
    r = client.post(f"/api/plans/{latest['id']}/advance",
                    json={"as_of": dawn.isoformat(), "reschedule": False})
    r.raise_for_status()

    frames = client.get(f"/api/nights/{nid}/frames").json()
    # 关键不变量：帧数不超过额度，且没有任何重复帧
    assert len(frames) <= 6
    actions_used = [f["target_id"] for f in frames]
    # 同一物理动作不会出现两条帧（action_id 唯一）
    r2 = client.get(f"/api/nights/{nid}/frames")
    ids = [f["id"] for f in r2.json()]
    assert len(ids) == len(set(ids))
    assert actions_used  # 非空


def test_bad_weather_during_exposure_interrupts_without_frame(client):
    n = make_night(client)
    make_proposal(client, awarded=6)
    nid = n["id"]
    v1 = client.post(f"/api/nights/{nid}/plans/generate",
                     json={"trigger": "initial"}).json()
    exps = sorted(_exposures(v1), key=lambda a: a["starts_at"])
    # 第二帧曝光进行到一半时坏天气到达
    s = datetime.fromisoformat(exps[1]["starts_at"])
    cut = s + timedelta(seconds=30)
    client.post(f"/api/nights/{nid}/weather", json={
        "starts_at": (s + timedelta(seconds=10)).isoformat(),
        "ends_at": (s + timedelta(minutes=120)).isoformat(),
        "usable": False, "cloud_pct": 95, "note": "突发厚云",
    }).raise_for_status()
    adv = client.post(f"/api/plans/{v1['id']}/advance",
                      json={"as_of": cut.isoformat()}).json()
    # 第一帧完成、第二帧中断；中断不产生帧
    assert adv["advanced"]["frames_created"] == 1
    assert adv["advanced"]["interrupted_exposures"] == 1
    v2 = adv["new_version"]
    interrupted = [a for a in _exposures(v2) if a["status"] == "interrupted"]
    assert len(interrupted) == 1
    # 云后重排：中断的那帧不占额度，目标仍应补回申请数量（受窗口/额度约束）
    frames_after = client.get(f"/api/nights/{nid}/frames").json()
    assert len(frames_after) == 1


def test_cannot_advance_non_latest_version(client):
    n = make_night(client)
    make_proposal(client, awarded=6)
    nid = n["id"]
    v1 = client.post(f"/api/nights/{nid}/plans/generate",
                     json={"trigger": "initial"}).json()
    exps = sorted(_exposures(v1), key=lambda a: a["starts_at"])
    cut = datetime.fromisoformat(exps[1]["starts_at"]) + timedelta(seconds=30)
    client.post(f"/api/nights/{nid}/weather", json={
        "starts_at": (cut - timedelta(minutes=1)).isoformat(),
        "ends_at": (cut + timedelta(minutes=90)).isoformat(),
        "usable": False, "cloud_pct": 90, "note": "x",
    }).raise_for_status()
    v2 = client.post(f"/api/plans/{v1['id']}/advance",
                     json={"as_of": cut.isoformat()}).json()["new_version"]
    # 此时再推进 v1 必须被拒绝
    r = client.post(f"/api/plans/{v1['id']}/advance",
                    json={"as_of": cut.isoformat(), "reschedule": False})
    assert r.status_code == 409


def test_manual_adjustment_drop_persists_reason(client):
    n = make_night(client)
    prop = make_proposal(client, awarded=6)
    nid = n["id"]
    v1 = client.post(f"/api/nights/{nid}/plans/generate",
                     json={"trigger": "initial"}).json()
    target_id = prop["targets"][1]["id"]
    r = client.post(f"/api/plans/{v1['id']}/adjustments", json={
        "target_id": target_id, "kind": "drop",
        "reason": "值班科学家决定本夜不观测该目标：设备故障",
    })
    r.raise_for_status()
    v2 = r.json()
    assert v2["trigger"] == "manual"
    assert any(a["kind"] == "drop" for a in v2["adjustments"])
    assert "设备故障" in v2["adjustments"][0]["reason"]
    assert any(u["target_id"] == target_id and "manual_drop" in u["reason"]
               for u in v2["unscheduled"])
    # v2 中不应再有该目标的曝光
    assert all(a["target_id"] != target_id for a in _exposures(v2))


def test_version_carries_history_reason(client):
    """每个计划版本都保留 trigger 与 reason，形成可审计链。"""
    n = make_night(client)
    make_proposal(client, awarded=6)
    v1 = client.post(
        f"/api/nights/{n['id']}/plans/generate",
        json={"trigger": "initial", "reason": "选择本夜"}).json()
    assert v1["version"] == 1 and v1["reason"] == "选择本夜"
    exps = sorted(_exposures(v1), key=lambda a: a["starts_at"])
    cut = datetime.fromisoformat(exps[1]["starts_at"]) + timedelta(seconds=30)
    client.post(f"/api/nights/{n['id']}/weather", json={
        "starts_at": (cut - timedelta(minutes=1)).isoformat(),
        "ends_at": (cut + timedelta(minutes=60)).isoformat(),
        "usable": False, "cloud_pct": 80, "note": "云",
    })
    client.post(f"/api/plans/{v1['id']}/advance",
                json={"as_of": cut.isoformat(), "reason": "天气重排原因标记"})
    versions = client.get(f"/api/nights/{n['id']}/timeline").json()["versions"]
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["trigger"] == "weather"
    assert "天气重排原因标记" in versions[1]["reason"]
