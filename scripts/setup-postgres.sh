#!/usr/bin/env bash
# 无 root 环境下从 Debian 包安装并启动用户态 PostgreSQL（一次性）。
set -euo pipefail

PREFIX="$HOME/opt/pgsql/usr"
PGDATA="$HOME/opt/pgdata"
PORT=55432
SOCKET="$PGDATA"

if [ ! -x "$PREFIX/lib/postgresql/15/bin/postgres" ]; then
  echo "[1/4] 下载 PostgreSQL 15 Debian 包…"
  mkdir -p "$HOME/opt/debs" && cd "$HOME/opt/debs"
  apt-get update -o Dir::State::Lists="$HOME/opt/apt/lists" -o Dir::Cache="$HOME/opt/apt/cache" >/dev/null 2>&1 || true
  apt-get download -o Dir::State::Lists="$HOME/opt/apt/lists" \
    postgresql-15 postgresql-client-15 postgresql-common postgresql-client-common \
    libpq5 libcommon-sense-perl libjson-perl libllvm14 libsensors-config libsensors5
  echo "[2/4] 解包到 $HOME/opt/pgsql …"
  mkdir -p "$HOME/opt/pgsql"
  for f in *.deb; do dpkg-deb -x "$f" "$HOME/opt/pgsql"; done
fi

export LD_LIBRARY_PATH="$PREFIX/lib/aarch64-linux-gnu:$PREFIX/lib/postgresql/15/lib"
PGBIN="$PREFIX/lib/postgresql/15/bin"

if [ ! -d "$PGDATA/base" ]; then
  echo "[3/4] initdb…"
  mkdir -p "$PGDATA"
  "$PGBIN/initdb" -D "$PGDATA" -U postgres --auth=trust -E UTF8 >/dev/null
  {
    echo "unix_socket_directories = '$SOCKET'"
    echo "listen_addresses = ''"
    echo "port = $PORT"
  } >> "$PGDATA/postgresql.conf"
fi

if ! "$PGBIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
  echo "[4/4] 启动 PostgreSQL（unix socket $SOCKET:$PORT）…"
  "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGDATA/server.log" start >/dev/null
  sleep 1
fi

PSQL="$PGBIN/psql -h $SOCKET -p $PORT -U postgres"
$PSQL -tAc "SELECT 1 FROM pg_database WHERE datname='nightplan'" | grep -q 1 \
  || $PSQL -c "CREATE DATABASE nightplan;"
$PSQL -tAc "SELECT 1 FROM pg_database WHERE datname='nightplan_test'" | grep -q 1 \
  || $PSQL -c "CREATE DATABASE nightplan_test;"

echo "PostgreSQL 就绪：$SOCKET:$PORT (nightplan, nightplan_test)"
