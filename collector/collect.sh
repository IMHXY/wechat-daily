#!/usr/bin/env bash
# macOS / Linux 一键脚本：导出当天消息 -> 归一化 -> 出日报
# 编排逻辑在 collector/daily.py，和 Windows 版 daily.bat 是同一套。

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

PYTHON="${PYTHON:-python3}"
exec "$PYTHON" daily.py "$@"
