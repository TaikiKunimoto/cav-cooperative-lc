#!/usr/bin/env bash
# ローカルのリポジトリを sgnlab へ同期する（コード送信のみ。リモートの結果は消さない）。
#
# 使い方:  scripts/remote/sgnlab_push.sh
#
# 送らないもの: .git / .venv / .gitignore 対象（結果CSV・out/ 配下・キャッシュ類）。
# --delete は使わない（リモート側で採った結果・ログを誤って消さないため）。

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

echo "[push] ${REPO_ROOT} -> ${SGNLAB_HOST}:~/${SGNLAB_DIR}"
ssh "${SGNLAB_HOST}" "mkdir -p \"\$HOME/${SGNLAB_DIR}\""

rsync -az --info=stats1 \
  --filter=':- .gitignore' \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.mypy_cache' \
  --exclude '.ruff_cache' \
  "${REPO_ROOT}/" "${SGNLAB_HOST}:${SGNLAB_DIR}/"

echo "[push] 依存を同期します（uv sync）"
remote_exec "uv sync 2>&1 | tail -2"
echo "[push] 完了。実行は scripts/remote/sgnlab_run.sh"
