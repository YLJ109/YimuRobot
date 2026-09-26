#!/usr/bin/env bash
# ============================================================
#  EFORT ER3-600 机器人仿真系统 — Linux / macOS 启动脚本
# ------------------------------------------------------------
#  usage:  ./start.sh          开发模式（vite :3000 + api :5000）
#          ./start.sh build    构建前端，全部由 :5000 提供
#          ./start.sh backend  只启动后端
#
#  Python 运行在 backend/venv（首次运行自动创建）。
# ============================================================
set -u

# ---- 定位脚本目录 ----
_src="${BASH_SOURCE[0]:-$0}"
_self_dir="$(cd -- "$(dirname -- "$_src")" 2>/dev/null && pwd)" || _self_dir=""
if [ -z "$_self_dir" ]; then
  case "$_src" in
    */*) _self_dir="$(cd -- "${_src%/*}" && pwd)" ;;
    *)   _self_dir="$(pwd)" ;;
  esac
fi
cd "$_self_dir" || exit 1
ROOT="$_self_dir"

MODE="${1:-dev}"
PY_BIN="${PY_BIN:-python3}"
command -v "$PY_BIN" >/dev/null 2>&1 || PY_BIN="python"
NPM_BIN="${NPM_BIN:-npm}"

VENV_PY="$ROOT/backend/venv/bin/python"
VENV_PIP="$ROOT/backend/venv/bin/pip"

echo "============================================================"
echo "  埃夫特 ER3-600 六轴机器人 3D 仿真系统"
echo "  工作目录: $ROOT"
echo "============================================================"

# ---- 环境检查 ----
command -v "$PY_BIN" >/dev/null 2>&1 || { echo "[X] 未找到 Python，请先安装 Python 3.10/3.11"; exit 1; }
if [ "$MODE" != "backend" ]; then
  command -v "$NPM_BIN" >/dev/null 2>&1 || { echo "[X] 未找到 npm，请先安装 Node.js 18+"; exit 1; }
fi

# ---- 创建 venv（若不存在）----
if [ ! -x "$VENV_PY" ]; then
  echo "[1/4] 创建虚拟环境 backend/venv ..."
  "$PY_BIN" -m venv "$ROOT/backend/venv" || { echo "[X] venv 创建失败"; exit 1; }
else
  echo "[1/4] venv ............. OK"
fi

# ---- .env 引导 ----
if [ ! -f backend/.env ]; then
  if [ -f backend/.env.example ]; then
    cp backend/.env.example backend/.env
    echo "[!] 已从 .env.example 生成 backend/.env"
    echo "    请填入真实的 ZHIPU_API_KEY，否则自然语言功能会降级"
  else
    echo "[!] 缺少 backend/.env，AI 功能将不可用"
  fi
fi

# ---- 依赖检查（缺失即安装进 venv，失败即中止）----
if ! "$VENV_PY" -c "import flask, flask_socketio, flask_cors, numpy, dotenv, faster_whisper, edge_tts" >/dev/null 2>&1; then
  echo "[2/4] 安装后端依赖到 venv ..."
  "$VENV_PIP" install -r backend/requirements.txt || { echo "[X] pip 安装失败"; exit 1; }
else
  echo "[2/4] 后端依赖 ........ OK"
fi

if [ "$MODE" != "backend" ] && [ ! -d frontend/node_modules ]; then
  echo "[3/4] 安装前端依赖 ..."
  ( cd frontend && "$NPM_BIN" install ) || { echo "[X] npm install 失败"; exit 1; }
else
  echo "[3/4] 前端依赖 ........ OK"
fi

# ---- 构建模式 ----
if [ "$MODE" = "build" ]; then
  echo "[4/4] 构建前端产物 ..."
  ( cd frontend && "$NPM_BIN" run build ) || { echo "[X] 构建失败"; exit 1; }
  echo
  echo "  构建完成，打开 http://localhost:5000"
  echo
fi

# ---- 启动 ----
PIDS=""
cleanup() {
  echo
  echo "正在停止服务 ..."
  for p in $PIDS; do kill "$p" 2>/dev/null || true; done
  exit 0
}
trap cleanup INT TERM

echo "启动后端 ..."
( cd backend && exec "$VENV_PY" app.py ) &
PIDS="$PIDS $!"
echo "  后端 PID: $!"

if [ "$MODE" != "backend" ] && [ "$MODE" != "build" ]; then
  echo "启动前端 ..."
  ( cd frontend && exec "$NPM_BIN" run dev ) &
  PIDS="$PIDS $!"
  echo "  前端 PID: $!"
  echo
  echo "  前端: http://localhost:3000"
else
  echo
fi
echo "  后端: http://localhost:5000"
echo "  健康检查: http://localhost:5000/api/health"
echo "  按 Ctrl+C 停止全部服务"
echo

wait
