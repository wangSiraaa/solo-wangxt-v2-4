"""运行参数：可通过环境变量覆盖。

默认使用本地 SQLite，便于零配置演示；生产/容器中设置
DATABASE_URL=postgresql+psycopg2://... 即可切换到 PostgreSQL。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR.parent / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{(DATA_DIR / 'observatory.db').as_posix()}",
)

# 观测站（演示用，可在创建观测夜时覆盖）
OBS_LAT = float(os.getenv("OBS_LAT", "30.0"))
OBS_LON = float(os.getenv("OBS_LON", "-110.0"))
OBS_HEIGHT = float(os.getenv("OBS_HEIGHT", "2200.0"))

# 可见性分析网格步长（秒）
GRID_STEP_SEC = int(os.getenv("GRID_STEP_SEC", "60"))

# 默认约束
DEFAULT_MIN_ALTITUDE = float(os.getenv("DEFAULT_MIN_ALTITUDE", "30.0"))
DEFAULT_MOON_SEPARATION = float(os.getenv("DEFAULT_MOON_SEPARATION", "50.0"))

# 单次动作耗时（秒）
SETUP_TARGET_SECONDS = 180   # 找星/导星校准
READOUT_SECONDS = 20         # 单帧读出
FILTER_PRESET_SECONDS = 10   # 滤镜机构就位（与切换同时发生时的基础动作）

# 滤镜 -> 所需暗天光等级（按太阳高度）与该滤镜进入光路的切换耗时
FILTERS: dict[str, dict] = {
    "U":  {"label": "U / 紫外蓝",   "dark_level": "astronomical", "switch_seconds": 45},
    "B":  {"label": "B / 蓝",       "dark_level": "astronomical", "switch_seconds": 30},
    "V":  {"label": "V / 可见光",   "dark_level": "astronomical", "switch_seconds": 20},
    "g":  {"label": "g / g 波段",   "dark_level": "astronomical", "switch_seconds": 35},
    "R":  {"label": "R / 红",       "dark_level": "nautical",     "switch_seconds": 20},
    "r":  {"label": "r / r 波段",   "dark_level": "nautical",     "switch_seconds": 25},
    "I":  {"label": "I / 近红",     "dark_level": "nautical",     "switch_seconds": 25},
    "i":  {"label": "i / i 波段",   "dark_level": "nautical",     "switch_seconds": 25},
    "L":  {"label": "L / 白光",     "dark_level": "nautical",     "switch_seconds": 20},
    "z":  {"label": "z / z 波段",   "dark_level": "civil",        "switch_seconds": 30},
    "Ha": {"label": "Hα / 窄带",    "dark_level": "astronomical", "switch_seconds": 60},
}

# 暗天光等级对应的太阳地平高度阈值（度）
DARK_SUN_ALT = {
    "civil": -6.0,
    "nautical": -12.0,
    "astronomical": -18.0,
}

FILTER_COLORS = {
    "U": "#7c6cff", "B": "#4f8cff", "V": "#37b6a7", "g": "#2fa36a",
    "R": "#e0a23a", "r": "#d98a2b", "I": "#c95f9b", "i": "#b84a86",
    "L": "#8aa0b4", "z": "#9a6fd0", "Ha": "#e05d5d",
}
