"""pytest 共享 fixture：独立临时 SQLite + TestClient。"""
from __future__ import annotations

import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="obs-test-")
os.environ["DATA_DIR"] = _tmp
# 网格用 2 分钟以加速天文计算（测试不追求 1 分钟精度）
os.environ["GRID_STEP_SEC"] = "120"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db, engine  # noqa: E402
from app import models  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    models.Base.metadata.drop_all(bind=engine)
    init_db()
    with TestClient(app) as c:
        yield c


def make_proposal(client, awarded: int = 10, targets=None) -> dict:
    targets = targets or [
        {"name": "M31", "ra_hours": 0.712, "dec_deg": 41.27, "filter": "I",
         "exposure_sec": 300, "requested_frames": 3, "priority": 10},
        {"name": "M45", "ra_hours": 3.783, "dec_deg": 24.12, "filter": "B",
         "exposure_sec": 200, "requested_frames": 3, "priority": 20},
    ]
    r = client.post("/api/proposals", json={
        "code": f"PROP-{os.urandom(3).hex()}", "pi": "t", "awarded_frames": awarded,
        "targets": targets,
    })
    r.raise_for_status()
    return r.json()


def make_night(client, date: str = "2026-09-18") -> dict:
    r = client.post("/api/nights", json={
        "local_date": date, "lat": 31.95, "lon": -111.6,
        "height_m": 2100, "timezone": "America/Phoenix",
    })
    r.raise_for_status()
    return r.json()
