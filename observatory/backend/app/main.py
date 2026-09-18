"""FastAPI 应用：提案、观测夜、天气、可见性、计划版本、执行模拟、人工调整。"""
from __future__ import annotations

from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import astronomy, executor, scheduler
from .config import FILTERS, DEFAULT_MIN_ALTITUDE, DEFAULT_MOON_SEPARATION, GRID_STEP_SEC
from .db import get_db, init_db
from .models import (
    Frame, ManualAdjustment, Night, PlanVersion, Proposal, ProposalStatus, Target,
)
from . import schemas as S


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="天文台夜间计划服务", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


def _get_or_404(db: Session, model, obj_id: str, name: str = "对象"):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(404, f"{name}不存在: {obj_id}")
    return obj


def _iso_pair(pair) -> list[str]:
    return [pair[0].isoformat(), pair[1].isoformat()]


def _iso_pairs(pairs) -> list[list[str]]:
    return [[a.isoformat(), b.isoformat()] for a, b in pairs]


# ---------------------------------------------------------------- meta / filters

@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "filters": FILTERS,
        "defaults": {
            "min_altitude": DEFAULT_MIN_ALTITUDE,
            "min_moon_sep": DEFAULT_MOON_SEPARATION,
            "grid_step_sec": GRID_STEP_SEC,
        },
    }


# ---------------------------------------------------------------- proposals

@app.post("/api/proposals", response_model=S.ProposalOut, status_code=201)
def create_proposal(body: S.ProposalIn) -> S.ProposalOut:
    db = next(get_db())
    try:
        if db.scalar(select(Proposal).where(Proposal.code == body.code)):
            raise HTTPException(409, f"提案编号 {body.code} 已存在")
        p = Proposal(code=body.code, pi=body.pi, title=body.title,
                     awarded_frames=body.awarded_frames, status=ProposalStatus.submitted)
        db.add(p)
        db.flush()
        for t in body.targets:
            db.add(Target(
                proposal_id=p.id, name=t.name, ra_deg=t.ra, dec_deg=t.dec_deg,
                filter=t.filter, exposure_sec=t.exposure_sec,
                requested_frames=t.requested_frames,
                min_altitude=t.min_altitude, min_moon_sep=t.min_moon_sep,
                priority=t.priority,
            ))
        db.commit()
        db.refresh(p)
        return S.ProposalOut.model_validate(p)
    finally:
        db.close()


@app.get("/api/proposals", response_model=list[S.ProposalOut])
def list_proposals() -> list[S.ProposalOut]:
    db = next(get_db())
    try:
        return [S.ProposalOut.model_validate(p)
                for p in db.scalars(select(Proposal).order_by(Proposal.created_at)).all()]
    finally:
        db.close()


@app.get("/api/proposals/{proposal_id}", response_model=S.ProposalOut)
def get_proposal(proposal_id: str) -> S.ProposalOut:
    db = next(get_db())
    try:
        p = _get_or_404(db, Proposal, proposal_id, "提案")
        return S.ProposalOut.model_validate(p)
    finally:
        db.close()


# ---------------------------------------------------------------- nights / weather

@app.post("/api/nights", response_model=S.NightOut, status_code=201)
def create_night(body: S.NightIn) -> S.NightOut:
    from zoneinfo import ZoneInfo
    db = next(get_db())
    try:
        try:
            ZoneInfo(body.timezone)
        except Exception:
            raise HTTPException(422, f"无效时区 {body.timezone}")
        if db.scalar(select(Night).where(Night.local_date == body.local_date)):
            raise HTTPException(409, f"观测夜 {body.local_date} 已存在")
        n = Night(**body.model_dump())
        db.add(n)
        db.commit()
        db.refresh(n)
        return S.NightOut.model_validate(n)
    finally:
        db.close()


@app.get("/api/nights", response_model=list[S.NightOut])
def list_nights() -> list[S.NightOut]:
    db = next(get_db())
    try:
        return [S.NightOut.model_validate(n)
                for n in db.scalars(select(Night).order_by(Night.local_date)).all()]
    finally:
        db.close()


@app.get("/api/nights/{night_id}", response_model=S.NightOut)
def get_night(night_id: str) -> S.NightOut:
    db = next(get_db())
    try:
        return S.NightOut.model_validate(_get_or_404(db, Night, night_id, "观测夜"))
    finally:
        db.close()


@app.post("/api/nights/{night_id}/weather", response_model=S.WeatherOut, status_code=201)
def add_weather(night_id: str, body: S.WeatherIn) -> S.WeatherOut:
    db = next(get_db())
    try:
        n = _get_or_404(db, Night, night_id, "观测夜")
        if body.ends_at <= body.starts_at:
            raise HTTPException(422, "ends_at 必须晚于 starts_at")
        from .models import WeatherSample
        w = WeatherSample(night_id=n.id, **body.model_dump())
        db.add(w)
        db.commit()
        db.refresh(w)
        return S.WeatherOut.model_validate(w)
    finally:
        db.close()


# ---------------------------------------------------------------- visibility

def _compute_visibility(db: Session, night: Night, targets: list[Target]) -> list[dict]:
    location = astronomy.make_location(night.lat, night.lon, night.height_m)
    nw = astronomy.night_windows(night.local_date, location, night.timezone)
    horizon = nw.horizon
    if horizon is None:
        raise HTTPException(422, "该纬度/日期没有日出日落（极昼/极夜）")
    out = []
    for t in targets:
        dark = nw.dark_for_level(FILTERS[t.filter]["dark_level"])
        tw = astronomy.target_windows(
            t.ra_deg, t.dec_deg, location, horizon, dark,
            t.min_altitude or DEFAULT_MIN_ALTITUDE,
            t.min_moon_sep or DEFAULT_MOON_SEPARATION,
        )
        out.append({
            "target_id": t.id,
            "name": t.name,
            "filter": t.filter,
            "dark_level": FILTERS[t.filter]["dark_level"],
            "min_altitude": t.min_altitude or DEFAULT_MIN_ALTITUDE,
            "min_moon_sep": t.min_moon_sep or DEFAULT_MOON_SEPARATION,
            "alt_windows": _iso_pairs(tw.alt_windows),
            "moon_windows": _iso_pairs(tw.moon_windows),
            "dark_windows": _iso_pairs(tw.dark_windows),
            "feasible": _iso_pairs(tw.feasible),
            "moon_illumination": tw.moon_illumination,
            "samples": tw.samples,
        })
    return out


@app.get("/api/nights/{night_id}/visibility")
def visibility(night_id: str, proposal_id: str | None = None) -> dict[str, Any]:
    db = next(get_db())
    try:
        n = _get_or_404(db, Night, night_id, "观测夜")
        stmt = select(Target)
        if proposal_id:
            stmt = stmt.where(Target.proposal_id == proposal_id)
        targets = list(db.scalars(stmt.order_by(Target.priority)).all())
        location = astronomy.make_location(n.lat, n.lon, n.height_m)
        nw = astronomy.night_windows(n.local_date, location, n.timezone)
        return {
            "night_id": n.id,
            "night_summary": nw.summary,
            "targets": _compute_visibility(db, n, targets),
        }
    finally:
        db.close()


# ---------------------------------------------------------------- planning

def _all_adjustments(db: Session, night_id: str) -> list[ManualAdjustment]:
    return list(db.scalars(
        select(ManualAdjustment)
        .join(PlanVersion, PlanVersion.id == ManualAdjustment.plan_version_id)
        .where(PlanVersion.night_id == night_id)
        .order_by(ManualAdjustment.created_at)
    ).all())


@app.post("/api/nights/{night_id}/plans/generate", response_model=S.VersionOut, status_code=201)
def generate_plan(night_id: str, body: S.GeneratePlanIn) -> S.VersionOut:
    db = next(get_db())
    try:
        n = _get_or_404(db, Night, night_id, "观测夜")
        ov = scheduler.Overrides.from_adjustments(_all_adjustments(db, n.id))
        pv = scheduler.build_plan(
            db, n, trigger=body.trigger, reason=body.reason,
            as_of=body.as_of, overrides=ov,
        )
        if body.adjustments:
            for adj in body.adjustments:
                db.add(ManualAdjustment(
                    plan_version_id=pv.id, target_id=adj.target_id, kind=adj.kind,
                    reason=adj.reason, author=adj.author, payload=adj.payload,
                ))
        db.commit()
        db.refresh(pv)
        return S.VersionOut.model_validate(pv)
    except ValueError as e:
        db.rollback()
        raise HTTPException(422, str(e))
    finally:
        db.close()


@app.post("/api/plans/{version_id}/advance")
def advance_plan(version_id: str, body: S.AdvanceIn) -> dict[str, Any]:
    """推进执行到 as_of；可选地按当前天气重排未开始曝光（新版本）。"""
    db = next(get_db())
    try:
        pv = _get_or_404(db, PlanVersion, version_id, "计划版本")
        executor.ensure_latest(db, pv)
        result = executor.advance(db, pv, body.as_of)
        db.commit()

        new_version = None
        if body.reschedule:
            db.refresh(pv)
            ov = scheduler.Overrides.from_adjustments(_all_adjustments(db, pv.night_id))
            n2 = db.get(Night, pv.night_id)
            nv = scheduler.build_plan(
                db, n2, trigger="weather", reason=body.reason,
                as_of=body.as_of, overrides=ov,
            )
            db.commit()
            db.refresh(nv)
            new_version = S.VersionOut.model_validate(nv).model_dump(mode="json")
        db.refresh(pv)
        return {"advanced": result, "new_version": new_version}
    finally:
        db.close()


@app.post("/api/plans/{version_id}/adjustments", response_model=S.VersionOut, status_code=201)
def add_adjustment(version_id: str, adj: S.AdjustmentIn) -> S.VersionOut:
    """登记人工调整（含原因）并基于本夜全部历史调整生成下一个人工版本。"""
    db = next(get_db())
    try:
        pv = _get_or_404(db, PlanVersion, version_id, "计划版本")
        if adj.target_id and db.get(Target, adj.target_id) is None:
            raise HTTPException(404, f"目标不存在: {adj.target_id}")
        night = db.get(Night, pv.night_id)

        # Overrides 汇总本夜历史调整 + 本次调整；版本先生成以便关联外键
        overrides = scheduler.Overrides.from_adjustments(
            _all_adjustments(db, night.id) + [_adj_proxy(adj)])
        nv = scheduler.build_plan(
            db, night, trigger="manual",
            reason=f"人工调整（{adj.kind}）：{adj.reason}",
            as_of=None, overrides=overrides,
        )
        db.add(ManualAdjustment(
            plan_version_id=nv.id, target_id=adj.target_id, kind=adj.kind,
            reason=adj.reason, author=adj.author, payload=adj.payload,
        ))
        db.commit()
        db.refresh(nv)
        return S.VersionOut.model_validate(nv)
    finally:
        db.close()


def _adj_proxy(adj: S.AdjustmentIn):
    class _P:
        kind = adj.kind
        target_id = adj.target_id
        payload = adj.payload
    return _P()


@app.get("/api/plans/{version_id}", response_model=S.VersionOut)
def get_plan(version_id: str) -> S.VersionOut:
    db = next(get_db())
    try:
        return S.VersionOut.model_validate(_get_or_404(db, PlanVersion, version_id, "计划版本"))
    finally:
        db.close()


@app.get("/api/nights/{night_id}/frames", response_model=list[S.FrameOut])
def night_frames(night_id: str) -> list[S.FrameOut]:
    db = next(get_db())
    try:
        _get_or_404(db, Night, night_id, "观测夜")
        rows = db.scalars(
            select(Frame).where(Frame.night_id == night_id).order_by(Frame.acquired_at)
        ).all()
        return [S.FrameOut.model_validate(r) for r in rows]
    finally:
        db.close()


@app.get("/api/nights/{night_id}/timeline", response_model=S.TimelineOut)
def timeline(night_id: str, include_visibility: bool = True) -> S.TimelineOut:
    """前端一次拿全：夜间摘要、天气、全部计划版本、已采帧、目标、可见性。"""
    db = next(get_db())
    try:
        n = _get_or_404(db, Night, night_id, "观测夜")
        versions = list(db.scalars(
            select(PlanVersion).where(PlanVersion.night_id == n.id)
            .order_by(PlanVersion.version)
        ).all())
        frames = list(db.scalars(
            select(Frame).where(Frame.night_id == n.id).order_by(Frame.acquired_at)
        ).all())
        targets = list(db.scalars(select(Target).order_by(Target.priority)).all())
        vis = _compute_visibility(db, n, targets) if include_visibility else []
        return S.TimelineOut(
            night=S.NightOut.model_validate(n),
            summary=versions[-1].night_summary if versions else {},
            weather=[S.WeatherOut.model_validate(w) for w in n.weather_samples],
            versions=[S.VersionOut.model_validate(v) for v in versions],
            frames=[S.FrameOut.model_validate(f) for f in frames],
            targets=[S.TargetOut.model_validate(t) for t in targets],
            visibility=vis,
        )
    finally:
        db.close()
