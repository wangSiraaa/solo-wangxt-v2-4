"""站点、提案与目标录入接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Proposal, Site, Target, AcquiredFrame
from ..schemas import (
    ProposalIn,
    ProposalOut,
    SiteIn,
    SiteOut,
    TargetIn,
    TargetOut,
)

router = APIRouter()


@router.post("/sites", response_model=SiteOut, tags=["sites"])
def create_site(body: SiteIn, db: Session = Depends(get_db)):
    if db.scalar(select(Site).where(Site.name == body.name)):
        raise HTTPException(409, f"站点 {body.name} 已存在")
    site = Site(**body.model_dump())
    db.add(site)
    db.commit()
    db.refresh(site)
    return site


@router.get("/sites", response_model=list[SiteOut], tags=["sites"])
def list_sites(db: Session = Depends(get_db)):
    return list(db.scalars(select(Site).order_by(Site.id)))


@router.post("/proposals", response_model=ProposalOut, tags=["proposals"])
def create_proposal(body: ProposalIn, db: Session = Depends(get_db)):
    if db.scalar(select(Proposal).where(Proposal.code == body.code)):
        raise HTTPException(409, f"提案编号 {body.code} 已存在")
    p = Proposal(**body.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("/proposals", response_model=list[ProposalOut], tags=["proposals"])
def list_proposals(db: Session = Depends(get_db)):
    return list(db.scalars(select(Proposal).order_by(Proposal.id)))


@router.get("/proposals/{proposal_id}", response_model=ProposalOut, tags=["proposals"])
def get_proposal(proposal_id: int, db: Session = Depends(get_db)):
    p = db.get(Proposal, proposal_id)
    if p is None:
        raise HTTPException(404, "提案不存在")
    return _with_acquired(p, db)


def _with_acquired(p: Proposal, db: Session) -> dict:
    out = {
        "id": p.id,
        "code": p.code,
        "pi_name": p.pi_name,
        "title": p.title,
        "created_at": p.created_at,
        "targets": [],
    }
    for t in p.targets:
        d = {c.name: getattr(t, c.name) for c in Target.__table__.columns}
        d["acquired_frames"] = (
            db.query(AcquiredFrame).filter(AcquiredFrame.target_id == t.id).count()
        )
        out["targets"].append(d)
    return out


@router.post(
    "/proposals/{proposal_id}/targets",
    response_model=TargetOut,
    tags=["proposals"],
)
def add_target(proposal_id: int, body: TargetIn, db: Session = Depends(get_db)):
    p = db.get(Proposal, proposal_id)
    if p is None:
        raise HTTPException(404, "提案不存在")
    t = Target(proposal_id=proposal_id, **body.model_dump())
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@router.get("/targets", response_model=list[TargetOut], tags=["proposals"])
def list_targets(db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Target).order_by(Target.id)))
    out = []
    for t in rows:
        d = {c.name: getattr(t, c.name) for c in t.__table__.columns}
        d["acquired_frames"] = (
            db.query(AcquiredFrame).filter(AcquiredFrame.target_id == t.id).count()
        )
        out.append(d)
    return out
