"""应用配置。

数据库默认连接本机用户态 PostgreSQL（见 README 启动方式），
可用环境变量 DATABASE_URL 覆盖（例如 docker-compose 中的服务名 postgres）。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _default_database_url() -> str:
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    socket = os.path.expanduser("~/opt/pgdata")
    return f"postgresql+psycopg2://postgres@/nightplan?host={socket}&port=55432"


DATABASE_URL = _default_database_url()

# 排程默认参数（分钟）
DEFAULT_SETUP_MINUTES = 10          # 设备准备（寻星、导星校准）
DEFAULT_FILTER_CHANGE_MINUTES = 3   # 每次滤镜切换
DEFAULT_READOUT_MINUTES = 1         # 每帧读出 / 相机开销
DEFAULT_MIN_ALTITUDE_DEG = 30.0
DEFAULT_MIN_MOON_SEPARATION_DEG = 40.0
GRID_MINUTES = 2               # 排程时间轴网格（分钟）
TIMELINE_GRID_MINUTES = 5     # 人工拖拽吸附网格

# Astropy 不自动联网下载 IERS 数据，使用离线内置表
os.environ.setdefault("ASTROPY_IERS_AUTO_DOWNLOAD", "0")
