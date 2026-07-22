#!/usr/bin/env bash
# sgnlab の実行結果をローカルへ回収し、manifest をマージする。
#
# 使い方:  scripts/remote/sgnlab_fetch.sh [--take-remote]
#
#   （既定）        ローカルに無い CSV だけ取り込む。同名のローカル既存 CSV は保持し、
#                   内容がリモートと異なるものは件数を警告表示する。
#   --take-remote   リモート側を正とする（リモートで --force 再採取した後の回収用）。
#                   上書き前にローカル raw/ 全体を raw.backup.<日時>/ へ退避する。
#
# 安全設計:
#   - ヘッダのみ（データ行なし）のローカル CSV は失敗 run の残骸なので先に除去し、
#     リモートの成功 CSV が取り込まれるようにする。
#   - manifest はキー単位マージ。既定はローカル優先（--take-remote 時はリモート優先）で、
#     マージ前に manifest.backup.<日時>.json を自動保存する。

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

TAKE_REMOTE=0
for a in "$@"; do
  case "$a" in
    --take-remote) TAKE_REMOTE=1 ;;
    *) echo "[fetch] 不明な引数: $a" && exit 1 ;;
  esac
done

LOCAL_OUT="${REPO_ROOT}/scripts/eval/out"
RAW="${LOCAL_OUT}/raw"
mkdir -p "${RAW}" "${LOCAL_OUT}/logs"

# 失敗 run の残骸（ヘッダのみ＝2行未満の CSV）を除去し、リモートの成功結果を取り込めるようにする
REMOVED=$(find "${RAW}" -name '*.csv' -print0 2>/dev/null | while IFS= read -r -d '' f; do
  if [[ $(wc -l < "$f") -lt 2 ]]; then rm "$f"; echo "$f"; fi
done | wc -l | tr -d ' ')
if [[ "${REMOVED}" -gt 0 ]]; then
  echo "[fetch] ヘッダのみのローカルCSV（失敗runの残骸）${REMOVED} 件を除去しました"
fi

if [[ "${TAKE_REMOTE}" -eq 1 ]]; then
  BACKUP="${LOCAL_OUT}/raw.backup.$(date +%Y%m%d-%H%M%S)"
  echo "[fetch] --take-remote: ローカル raw/ を ${BACKUP} へ退避してからリモートで上書きします"
  cp -a "${RAW}" "${BACKUP}"
  rsync -az --info=stats1 \
    "${SGNLAB_HOST}:${SGNLAB_DIR}/scripts/eval/out/raw/" "${RAW}/"
  PREFER=remote
else
  echo "[fetch] raw CSV を回収（ローカルに無いものだけ。既存は上書きしません）"
  rsync -az --info=stats1 --ignore-existing \
    "${SGNLAB_HOST}:${SGNLAB_DIR}/scripts/eval/out/raw/" "${RAW}/"
  # 内容が異なるのに --ignore-existing でスキップされた同名ファイルを検出して知らせる
  DIFFN=$(rsync -rnc --existing --out-format='%n' \
    "${SGNLAB_HOST}:${SGNLAB_DIR}/scripts/eval/out/raw/" "${RAW}/" 2>/dev/null | grep -c '\.csv$' || true)
  if [[ "${DIFFN}" -gt 0 ]]; then
    echo "[fetch] ⚠ リモートと内容が異なるローカル既存CSVが ${DIFFN} 件あります（取り込まれていません）。"
    echo "        リモートで --force 再採取した結果を採用する場合: scripts/remote/sgnlab_fetch.sh --take-remote"
  fi
  PREFER=local
fi

echo "[fetch] 実行ログを回収"
rsync -az --info=stats1 --ignore-existing \
  "${SGNLAB_HOST}:${SGNLAB_DIR}/scripts/eval/out/logs/" "${LOCAL_OUT}/logs/" || echo "(logs なし)"

echo "[fetch] manifest を回収してマージ（優先: ${PREFER}）"
if rsync -az "${SGNLAB_HOST}:${SGNLAB_DIR}/scripts/eval/out/manifest.json" "${LOCAL_OUT}/manifest.sgnlab.json"; then
  (cd "${REPO_ROOT}" && uv run python scripts/remote/merge_manifests.py \
    "${LOCAL_OUT}/manifest.sgnlab.json" --into "${LOCAL_OUT}/manifest.json" --prefer "${PREFER}")
else
  echo "[fetch] リモートに manifest.json がありません（スイープ未完了の可能性）"
  exit 1
fi

echo "[fetch] 完了。集計: uv run python scripts/eval/run_all.py --skip-sweep"
