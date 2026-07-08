#!/usr/bin/env bash
# sgnlab リモート評価スクリプトの共通設定。
# 各値は環境変数で上書き可能（例: SGNLAB_HOST=other-host ./sgnlab_push.sh）。

set -euo pipefail

# ~/.ssh/config の Host 名（Tailscale 経由）
SGNLAB_HOST="${SGNLAB_HOST:-sgnlab}"
# リモート側の作業ディレクトリ（リモート $HOME からの相対パス）
SGNLAB_DIR="${SGNLAB_DIR:-eval/high-way-branch-v2}"
# リモート側の SUMO_HOME（Ubuntu の apt 版既定。非対話 ssh では .bashrc が読まれないため明示）
SGNLAB_SUMO_HOME="${SGNLAB_SUMO_HOME:-/usr/share/sumo}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# リモートでコマンドを実行する（SUMO_HOME と uv の PATH を通した上で）
remote_exec() {
  # shellcheck disable=SC2029  # リモート側で展開させたい変数はエスケープ済み
  ssh "${SGNLAB_HOST}" "export SUMO_HOME='${SGNLAB_SUMO_HOME}'; export PATH=\"\$HOME/.local/bin:\$PATH\"; cd \"\$HOME/${SGNLAB_DIR}\" && $*"
}
