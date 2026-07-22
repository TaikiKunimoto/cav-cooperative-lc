#!/usr/bin/env bash
# ローカルのリポジトリを sgnlab へ同期する（コード送信のみ。リモートの結果は消さない）。
#
# 使い方:  scripts/remote/sgnlab_push.sh
#
# 送らないもの: .git / .venv / .gitignore 対象（結果CSV・out/ 配下・キャッシュ類）。
# --delete は使わない（リモート側で採った結果・ログを誤って消さないため）。
# .git を送らない代わりに、git 来歴（コミット・branch・dirty）を .sync_metadata.json として
# 同梱する（リモートの run_sweep が manifest の来歴に使う）。

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

# スイープ実行中の push はコード差し替えで新旧コミットの結果が混在するため拒否する
echo "[push] リモートでスイープが実行中でないか確認..."
if remote_exec "pgrep -af '[r]un_sweep' > /dev/null" 2>/dev/null; then
  echo "[push] 中止: sgnlab でスイープが実行中です。完了を待つか、リモートで停止してから push してください。"
  echo "       進捗確認: scripts/remote/sgnlab_status.sh"
  exit 1
fi

echo "[push] ${REPO_ROOT} -> ${SGNLAB_HOST}:~/${SGNLAB_DIR}"
ssh "${SGNLAB_HOST}" "mkdir -p \"\$HOME/${SGNLAB_DIR}\""

# git 来歴ファイルを生成して同期対象に含める（rsync 後にリモートへ単独送信）
SYNC_META="$(mktemp)"
trap 'rm -f "${SYNC_META}"' EXIT
{
  echo "{"
  echo "  \"git_commit\": \"$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo unknown)\","
  echo "  \"git_branch\": \"$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)\","
  echo "  \"git_dirty\": $(test -n "$(git -C "${REPO_ROOT}" status --porcelain 2>/dev/null)" && echo true || echo false),"
  echo "  \"pushed_at\": \"$(date +%Y-%m-%dT%H:%M:%S)\","
  echo "  \"pushed_from\": \"$(hostname)\""
  echo "}"
} > "${SYNC_META}"

rsync -az --info=stats1 \
  --filter=':- .gitignore' \
  --exclude '.git' \
  --exclude '.venv' \
  --exclude '.mypy_cache' \
  --exclude '.ruff_cache' \
  "${REPO_ROOT}/" "${SGNLAB_HOST}:${SGNLAB_DIR}/"
rsync -az "${SYNC_META}" "${SGNLAB_HOST}:${SGNLAB_DIR}/.sync_metadata.json"

echo "[push] 依存を同期します（uv sync）"
# リモート側で pipefail を立て、uv sync の失敗が tail に隠れないようにする
remote_exec "set -o pipefail; uv sync 2>&1 | tail -2"
echo "[push] 完了。実行は scripts/remote/sgnlab_run.sh"
