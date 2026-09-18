#!/usr/bin/env bash
# 一键演示：起库 -> 播种 -> 初始计划/天气中断重排/人工锁定/夜结束 的完整剧本（文本输出）
set -euo pipefail
cd "$(dirname "$0")/../backend"

bash ../scripts/setup-postgres.sh
# uv 若不存在则安装
if [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"
[ -d .venv ] || uv venv .venv
uv pip install --python .venv/bin/python -e . pytest -q
exec .venv/bin/python -m app.demo
