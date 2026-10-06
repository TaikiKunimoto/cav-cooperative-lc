#!/usr/bin/env bash
#
# Claude Code の永続メモリ保存先 (<config>/projects/<slug>/memory) を
# このリポジトリ内の .claude-memory/ へシンボリックリンクする。
#
# <config> はハーネスと同じ判定に合わせる: CLAUDE_CONFIG_DIR が設定されていれば
# それ、未設定なら既定の ~/.claude。これで機ごとに config ディレクトリが違っても
# （例: MacBook=~/.claude-personal / Desktop=~/.claude）各機が自分の config 配下へ
# 正しくリンクを張れる。
#
# ハーネスはメモリ保存先をリポジトリの絶対パスから決定するため、
# 別デバイス/別パスに clone した場合はこのスクリプトを再実行すれば
# 正しいリンクが張り直される。
#
# 使い方:  bash scripts/link-memory.sh
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SLUG="$(printf '%s' "$REPO_DIR" | sed 's#/#-#g')"
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
MEM_PARENT="$CONFIG_DIR/projects/$SLUG"
LINK="$MEM_PARENT/memory"
TARGET="$REPO_DIR/.claude-memory"

mkdir -p "$TARGET"
mkdir -p "$MEM_PARENT"

# 既存リンクを張り替え
if [ -L "$LINK" ]; then
  rm "$LINK"
elif [ -d "$LINK" ]; then
  # 実ディレクトリだった場合は中身を退避してから差し替え
  shopt -s nullglob dotglob
  for f in "$LINK"/*; do
    mv -n "$f" "$TARGET"/ 2>/dev/null || true
  done
  shopt -u nullglob dotglob
  rmdir "$LINK" 2>/dev/null || rm -rf "$LINK"
fi

ln -s "$TARGET" "$LINK"

echo "linked: $LINK -> $TARGET"
