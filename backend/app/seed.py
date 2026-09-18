"""重置数据库并播种演示数据：兴隆站 + 两个提案 + 六个目标 + 观测夜。

用法: .venv/bin/python -m app.seed
"""
from __future__ import annotations

from sqlalchemy import select

from .database import Base, SessionLocal, engine
from .models import Night, NightTarget, Proposal, Site, Target

SITE = dict(
    name="国家天文台兴隆站",
    latitude_deg=40.3959,
    longitude_deg=117.5786,
    elevation_m=900.0,
    timezone="Asia/Shanghai",
)

NIGHT_DATE = "2026-09-18"

PROPOSALS = [
    dict(
        code="P2026-A07",
        pi_name="林知微",
        title="近邻星系与行星状星云多色测光",
        targets=[
            # M31：整夜高空、远离月亮，最高优先
            dict(name="M31", ra_deg=10.6847, dec_deg=41.2691, magnitude=3.4,
                 filter_name="V", exposure_seconds=600, requested_frames=4,
                 priority=1),
            # M57 指环星云：傍晚才升到安全高度
            dict(name="M57", ra_deg=283.3968, dec_deg=33.0292, magnitude=8.8,
                 filter_name="B", exposure_seconds=480, requested_frames=4,
                 priority=2),
            # M45：后半夜目标
            dict(name="M45", ra_deg=56.8700, dec_deg=24.1167, magnitude=1.6,
                 filter_name="R", exposure_seconds=300, requested_frames=3,
                 priority=2),
        ],
    ),
    dict(
        code="P2026-B12",
        pi_name="陈望舒",
        title="南半球球状星团与人马座天区监测",
        targets=[
            # ω Cen：从兴隆看整夜低于高度角阈值
            dict(name="omega Cen", ra_deg=201.6970, dec_deg=-47.4795,
                 magnitude=3.9, filter_name="V", exposure_seconds=600,
                 requested_frames=2, priority=3),
            # 人马座候选体：高度够，但与当夜月亮角距不足 40°
            dict(name="Sgr-候选 J1732-1200", ra_deg=263.0000, dec_deg=-12.0000,
                 magnitude=11.2, filter_name="V", exposure_seconds=600,
                 requested_frames=2, priority=3),
            # NGC 891：后半夜侧向星系，填充深夜窗口
            dict(name="NGC 891", ra_deg=35.5700, dec_deg=42.3500,
                 magnitude=10.0, filter_name="I", exposure_seconds=720,
                 requested_frames=3, priority=3),
        ],
    ),
]


def reset_and_seed() -> dict:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        site = Site(**SITE)
        db.add(site)
        db.flush()

        targets_by_name: dict[str, Target] = {}
        for p_in in PROPOSALS:
            proposal = Proposal(
                code=p_in["code"], pi_name=p_in["pi_name"], title=p_in["title"]
            )
            db.add(proposal)
            db.flush()
            for t_in in p_in["targets"]:
                t = Target(proposal_id=proposal.id, **t_in)
                db.add(t)
                db.flush()
                targets_by_name[t.name] = t

        night = Night(site_id=site.id, night_date=NIGHT_DATE)
        db.add(night)
        db.flush()
        for t in targets_by_name.values():
            db.add(NightTarget(night_id=night.id, target_id=t.id))
        db.commit()
        return {
            "site_id": site.id,
            "night_id": night.id,
            "targets": {name: t.id for name, t in targets_by_name.items()},
        }
    finally:
        db.close()


if __name__ == "__main__":
    refs = reset_and_seed()
    print("播种完成：", refs)
