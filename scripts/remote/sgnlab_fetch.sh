#!/usr/bin/env bash
# sgnlab の実行結果をローカルへ回収し、manifest をマージする。
#
# 使い方:  scripts/remote/sgnlab_fetch.sh
#
# 安全設計:
#   - raw CSV は --ignore-existing（同名のローカル既存ファイルは保持＝上書きしない）。
#   - リモート manifest は manifest.sgnlab.json として取得し、merge_manifests.py が
#     ローカル manifest.json へキー単位でマージ（マージ前に manifest.backup.*.json を残す）。

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

LOCAL_OUT="${REPO_ROOT}/scripts/eval/out"
mkdir -p "${LOCAL_OUT}/raw" "${LOCAL_OUT}/logs"

echo "[fetch] raw CSV を回収（既存ローカルファイルは上書きしません）"
rsync -az --info=stats1 --ignore-existing \
  "${SGNLAB_HOST}:${SGNLAB_DIR}/scripts/eval/out/raw/" "${LOCAL_OUT}/raw/"

echo "[fetch] 実行ログを回収"
rsync -az --info=stats1 --ignore-existing \
  "${SGNLAB_HOST}:${SGNLAB_DIR}/scripts/eval/out/logs/" "${LOCAL_OUT}/logs/" || echo "(logs なし)"

echo "[fetch] manifest を回収してマージ"
if rsync -az "${SGNLAB_HOST}:${SGNLAB_DIR}/scripts/eval/out/manifest.json" "${LOCAL_OUT}/manifest.sgnlab.json"; then
  (cd "${REPO_ROOT}" && uv run python scripts/remote/merge_manifests.py \
    "${LOCAL_OUT}/manifest.sgnlab.json" --into "${LOCAL_OUT}/manifest.json")
else
  echo "[fetch] リモートに manifest.json がありません（スイープ未完了の可能性）"
  exit 1
fi

echo "[fetch] 完了。集計: uv run python scripts/eval/run_all.py --skip-sweep"
