#!/usr/bin/env bash
# 修复前端 ENOENT page.js 错误
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/frontend"

echo "清理 .next 缓存…"
rm -rf .next

echo "重新构建…"
npm run build

if [[ ! -f .next/server/app/page.js ]]; then
  echo "❌ 构建后仍缺少 page.js"
  exit 1
fi

echo "✅ 前端构建成功"
echo "请运行: cd $ROOT && ./start.sh"
