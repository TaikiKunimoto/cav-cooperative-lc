#!/usr/bin/env bash
# sgnlab 上のスイープ進捗を表示する（読み取りのみ・安全）。
#
# 使い方:  scripts/remote/sgnlab_status.sh

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

echo "=== 実行中プロセス ==="
# 文字クラス [r] 等でパターン文字列自身へのマッチを防ぐ（ssh のラッパーシェルが
# パターンを含むため、素のパターンだと常に自己マッチして「実行中」と誤表示する）。
remote_exec "pgrep -af '[r]un_sweep|python -m [v]2|python -m [v]1' || echo '(実行中の run はありません)'"

echo ""
echo "=== 生成済み run CSV ==="
remote_exec "ls scripts/eval/out/raw/*.csv 2>/dev/null | wc -l | xargs echo 件数: "

echo ""
echo "=== 最新ログ（末尾15行） ==="
remote_exec "ls -t scripts/eval/out/sweep_*.log 2>/dev/null | head -1 | xargs -r tail -15 || echo '(ログなし)'"
