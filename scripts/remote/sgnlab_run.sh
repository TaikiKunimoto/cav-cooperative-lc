#!/usr/bin/env bash
# sgnlab 上で評価スイープをバックグラウンド起動する。
#
# 使い方:  scripts/remote/sgnlab_run.sh [--yes] [run_sweep.py への引数...]
#   例:    scripts/remote/sgnlab_run.sh --suite proposed --quick
#          scripts/remote/sgnlab_run.sh --yes --suite proposed --workers 20
#
# 安全設計:
#   - 実行前に --dry-run でジョブ数を表示し、確認プロンプトを出す（--yes でスキップ）。
#   - 既存CSVがある run は run_sweep 側の冪等スキップが効く（--force を渡さない限り再実行しない）。

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

CONFIRM=1
ARGS=()
for a in "$@"; do
  if [[ "$a" == "--yes" ]]; then CONFIRM=0; else ARGS+=("$a"); fi
done
if [[ ${#ARGS[@]} -eq 0 ]]; then
  ARGS=(--suite proposed)
fi

echo "[run] ジョブ数を確認中..."
N_JOBS=$(remote_exec "uv run python scripts/eval/run_sweep.py ${ARGS[*]} --dry-run | grep -c '^  '" || true)
echo "[run] 投入予定: ${N_JOBS} jobs  (args: ${ARGS[*]})"

if [[ "${CONFIRM}" -eq 1 ]]; then
  read -r -p "sgnlab で ${N_JOBS} jobs を実行します。よろしいですか? [y/N] " ans
  if [[ "${ans}" != "y" && "${ans}" != "Y" ]]; then
    echo "[run] 中止しました。"
    exit 1
  fi
fi

STAMP=$(date +%Y%m%d-%H%M%S)
LOG="scripts/eval/out/sweep_${STAMP}.log"
remote_exec "mkdir -p scripts/eval/out && nohup uv run python scripts/eval/run_sweep.py ${ARGS[*]} > ${LOG} 2>&1 & echo \"[run] 起動 PID=\$!\""
echo "[run] リモートログ: ~/${SGNLAB_DIR}/${LOG}"
echo "[run] 進捗確認: scripts/remote/sgnlab_status.sh / 回収: scripts/remote/sgnlab_fetch.sh"
