# 天文台夜间计划系统（Observatory Night Planner）

把观测**提案**转换成可执行的**夜间计划**：申请人录入目标坐标、曝光时长和滤镜；值班科学家
选择观测夜后，系统用 Astropy 计算并**解释**每个目标因高度角、月距或晨昏蒙影窗口而能否执行；
天气缩短窗口时只重排**未开始**的曝光，已采集的帧不会再次占用申请额度；人工调整的原因与
计划版本全程留痕。

> 纯软件模拟，**不连接真实望远镜**：用本地天气样本演示中断、重排与观测完成。

## 技术栈

| 层 | 技术 |
|---|---|
| 计算服务 | FastAPI + Astropy（晨昏蒙影 / 高度角 / 月距 / 月相） |
| 持久化 | PostgreSQL（SQLAlchemy 2.0；本地默认 SQLite 零配置，切 `DATABASE_URL` 即用 PG） |
| 前端 | React 18 + Vite，自绘 SVG 夜间时间轴 |

## 快速开始（本地零配置，SQLite）

```bash
# 后端
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/seed_demo.py     # 端到端演示剧情，打印三个版本的时间轴
.venv/bin/uvicorn app.main:app --reload   # http://localhost:8000/docs

# 前端
cd frontend
npm install && npm run dev                # http://localhost:5173 （/api 代理到 8000）
```

`seed_demo.py` 每次会重建本地库并跑完整剧情：初始排程 → 夜中卷云关闭圆顶 →
中断/重排 → 人工置顶（留原因）→ 天气恢复 → 夜末结算，并断言帧额度不变量。

## Docker Compose（PostgreSQL）

```bash
docker compose up --build
# API:  http://localhost:8000/docs
# Web:  http://localhost:8080
```

## 领域模型与关键不变量

- `Proposal / Target`：申请目标（RA/Dec、滤镜、单帧曝光、申请帧数）与提案**总帧额度**。
- `Night / WeatherSample`：观测夜（站点、时区）与本地天气时段（`usable=false` 即关闭圆顶）。
- `PlanVersion`：每次排程产生不可变新版本，保留 `trigger`（initial/weather/manual/completion）
  与 `reason`；`unscheduled` 快照记录排不进的目标及原因。
- `PlanAction`：`setup`（设备准备）/ `filter_change`（滤镜切换）/ `exposure`（曝光），
  含起止时间、状态与历史冻结标记 `carries_frame`。
- `Frame`：**只有曝光（含读出）完整完成才创建**，是占用申请额度的唯一记录。

**排程规则**

1. 曝光必须同时落在：目标高度角 ≥ 阈值、与月球角距 ≥ 阈值、滤镜要求的暗天光等级
   （r/I/L 航海蒙影，B/V/g/U/Ha 天文暗夜…）三者交集内。
2. 滤镜切换、设备准备占用真实时间，且只能在日落到日出、天气可用时段进行；
   同目标背靠背连拍免重复找星，换目标重新准备。
3. 提案帧额度在排程时跨目标实时递减——后面的目标不会超用。
4. 天气更新重排时，`as_of` 前**已完成**动作原样冻结到新版本（其帧不重复创建）；
   曝光进行中或读出时撞坏天气 → `interrupted`，帧丢失、**不占额度**，云后自动补排；
   未开始的动作整体丢弃重新排布。
5. 执行只能推进**最新版本**；旧版本仅作留档，禁止在其上凭空产生帧。
6. 人工调整（pin 置顶 / drop 移出 / reorder / 覆盖约束）必须填写原因，随版本保存。

## 主要 API

```
POST   /api/proposals                         录入提案与目标
POST   /api/nights                            建立观测夜
POST   /api/nights/{id}/weather               追加本地天气样本
GET    /api/nights/{id}/visibility            各目标高度/月距/晨昏/综合可行窗口 + 采样曲线
POST   /api/nights/{id}/plans/generate        生成初始计划
POST   /api/plans/{vid}/advance               推进执行到某时刻（可选重排→新版本）
POST   /api/plans/{vid}/adjustments           登记人工调整（含原因）→ 新版本
GET    /api/nights/{id}/timeline              前端一次取全：摘要/天气/版本/帧/可见性
GET    /api/nights/{id}/frames                已采集帧（额度占用）
```

## 测试

```bash
cd backend && .venv/bin/python -m pytest -q
# 9 个用例：可见性解释、滤镜/准备耗时、额度硬上限、动作不重叠、
# 曝光必须在可行窗口内、天气中断不重复计帧、禁止推进旧版本、人工调整留痕、版本原因链
```

## 目录

```
observatory/
├── backend/
│   ├── app/
│   │   ├── astronomy.py    # Astropy：晨昏蒙影分级、高度角、月距、月相、采样
│   │   ├── scheduler.py    # 排程核心：窗口交集、准备/滤镜、额度、跨版本冻结
│   │   ├── executor.py     # 执行模拟：完成落帧、天气中断、只推进最新版
│   │   ├── intervals.py    # 区间运算（交/并/差/裁剪）
│   │   ├── models.py       # SQLAlchemy 模型
│   │   ├── schemas.py      # Pydantic 模型
│   │   └── main.py         # FastAPI 路由
│   ├── scripts/seed_demo.py
│   └── tests/
├── frontend/src/           # React：时间轴 / 提案表单 / 控制台 / 版本与可见性面板
└── docker-compose.yml
```
