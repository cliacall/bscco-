#!/usr/bin/env bash
# 一键启动：Python 后端 API + 轻量 Demo 网页
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
ORANGE='\033[0;33m'
NC='\033[0m'

kill_port() {
  local port=$1
  local pids
  pids=$(lsof -ti:"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo -e "${ORANGE}释放端口 :$port …${NC}"
    echo "$pids" | xargs kill -9 2>/dev/null || true
    sleep 1
  fi
}

ensure_frontend_build() {
  local page_js="frontend/.next/server/app/page.js"
  if [[ ! -f "$page_js" ]]; then
    echo -e "${ORANGE}前端缓存损坏或缺失，重新构建…${NC}"
    rm -rf frontend/.next
  fi
  echo -e "${ORANGE}构建前端（production）…${NC}"
  (cd frontend && npm run build)
  if [[ ! -f "$page_js" ]]; then
    echo -e "${RED}前端构建失败：缺少 $page_js${NC}"
    exit 1
  fi
}

cleanup() {
  echo ""
  echo -e "${ORANGE}正在关闭...${NC}"
  [[ -n "$BACKEND_PID" ]] && kill "$BACKEND_PID" 2>/dev/null || true
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  echo -e "${GREEN}已退出${NC}"
  exit 0
}
trap cleanup SIGINT SIGTERM

# ── 检查 .env ──
if [[ ! -f .env ]]; then
  echo -e "${RED}缺少 .env，请先: cp .env.example .env${NC}"
  exit 1
fi
WEB_PORT_VALUE="${WEB_PORT:-8888}"

# ── 清理旧进程（避免 .next 被 kill -9 损坏）──
kill_port "$WEB_PORT_VALUE"
kill_port 3000

# ── Python 虚拟环境 ──
if [[ ! -d .venv ]]; then
  echo -e "${ORANGE}创建 Python 虚拟环境...${NC}"
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt -q
fi
source .venv/bin/activate

# ── 启动后端 ──
if [[ "${FULL_BOT:-0}" == "1" ]]; then
  echo -e "${ORANGE}启动完整机器人引擎（Telegram / GMGN / four.meme / Web API）...${NC}"
  python main.py &
else
  echo -e "${ORANGE}启动 Demo Web/API（最快模式）...${NC}"
  .venv/bin/uvicorn web.server:create_app --factory --host 0.0.0.0 --port "$WEB_PORT_VALUE" &
fi
BACKEND_PID=$!

READY=0
for i in {1..30}; do
  if curl -sf "http://localhost:${WEB_PORT_VALUE}/api/settings" >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done
if [[ "$READY" != "1" ]]; then
  echo -e "${RED}后端 30 秒内未就绪，请看上面的日志；常见原因是端口占用、.env 配置错误或外部 API 卡住。${NC}"
  exit 1
fi

if [[ "${USE_NEXT_UI:-0}" == "1" ]]; then
  # ── 可选启动 Next 前端 ──
  if [[ ! -d frontend/node_modules ]]; then
    echo -e "${ORANGE}安装前端依赖...${NC}"
    cd frontend && npm install && cd "$ROOT"
  fi
  if [[ ! -f frontend/.env.local ]]; then
    cp frontend/.env.local.example frontend/.env.local
  fi

  if [[ "${FRONTEND_DEV:-}" == "1" ]]; then
    echo -e "${ORANGE}启动前端 dev 模式…${NC}"
    rm -rf frontend/.next
    cd frontend
    npm run dev &
    FRONTEND_PID=$!
    cd "$ROOT"
  else
    ensure_frontend_build
    echo -e "${ORANGE}启动前端展示台（production）…${NC}"
    cd frontend
    npm run start &
    FRONTEND_PID=$!
    cd "$ROOT"
  fi

  for i in {1..30}; do
    code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:3000 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
      break
    fi
    sleep 1
  done
fi

echo ""
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "${GREEN}  ✅ 全部就绪${NC}"
echo -e "${ORANGE}  🌐 打开 Demo → http://localhost:${WEB_PORT_VALUE}${NC}"
if [[ "${USE_NEXT_UI:-0}" == "1" ]]; then
  echo -e "${ORANGE}  🌐 Next 前端 → http://localhost:3000${NC}"
fi
echo -e "${ORANGE}  🔌 API 数据 → http://localhost:${WEB_PORT_VALUE}/api/settings${NC}"
echo -e "${ORANGE}  📱 TG 启动后会收到「bscco启动成功」${NC}"
echo -e "${GREEN}════════════════════════════════════════${NC}"
echo -e "  按 Ctrl+C 停止所有服务"
echo -e "  如需旧 Next 面板: USE_NEXT_UI=1 ./start.sh"
echo -e "  如需完整机器人循环: FULL_BOT=1 AI_AUTO_TRADE=true ./start.sh"
echo ""

wait
