"""FastAPI 入口：观测提案 -> 夜间可执行计划。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from .routers import nights, proposals

app = FastAPI(
    title="NightPlan 天文台夜间排程服务",
    version="0.1.0",
    description=(
        "把观测提案（坐标/曝光/滤镜）转成可执行夜间时间轴；"
        "解释高度角、月距、晨昏窗口不可行原因；"
        "天气中断后只重排未开始曝光，已采集帧不重复占用申请额度。"
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(proposals.router)
app.include_router(nights.router)


@app.on_event("startup")
def _create_tables() -> None:
    Base.metadata.create_all(bind=engine)


@app.get("/", tags=["meta"])
def root():
    return {
        "name": "nightplan",
        "docs": "/docs",
        "features": [
            "提案与目标录入（坐标、曝光、滤镜、申请额度）",
            "高度角/月距/天文晨昏可行性解释",
            "设备准备与滤镜切换占用时间轴",
            "天气缩短窗口后只重排未开始曝光",
            "已采集帧台账不重复占用额度",
            "人工调整原因与计划版本审计",
        ],
    }
