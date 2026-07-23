---
name: python-toolchain-quirks
description: uv/ruff/mypy/pre-commit は移行完了。.gitignore の statistics パターンで ruff が素通りする罠など運用上の注意点
metadata: 
  node_type: memory
  type: project
  originSessionId: d7db6234-ee5a-41cb-bf74-40b65202abaa
  modified: 2026-07-23T21:11:55.362Z
---

poetry→uv 移行と後始末（ruff 0.15 / mypy 2.1 strict / pre-commit）は完了し、すべて main マージ済み（PR #2 / #4 / #5 / #6。traci・sumolib の 1.27 化も PR #17 で完了。2026-07-06 確認）。以下は今も残る運用上の注意点。

- **`.gitignore` の `statistics` パターンが広すぎる**（未対応）: `simulationStatistics/statistics/` 配下のソースまで ignore され、`ruff check .` は gitignore されたファイルを素通りする。pre-commit は明示パスで検査するのでそちらでは検出される。「lint が通った」ように見えても gitignore に飲まれていないか注意。
- **新デバイスのセットアップ手順**: `uv sync` → `uv run pre-commit install` → `bash scripts/link-memory.sh`（Claude メモリのリンク張り直し）。
- **IDE 表示の mypy エラーは古いキャッシュ由来のことがある**: CLI の `uv run mypy` の結果を正とする。
- **リポジトリのディレクトリ名を変えると .venv が壊れる**: entry point スクリプトの shebang が旧絶対パス
  （例 high-way-branch-v2）を指したまま残り、`uv run mypy` が「Failed to spawn: No such file or directory」で
  落ちる（ruff はネイティブバイナリなので無事＝気づきにくい）。対処は `rm -rf .venv && uv sync`（2026-07-24 遭遇）。
- **バックグラウンド実行の PID 名前空間隔離**: この環境ではバックグラウンドタスクの PID 名前空間が隔離され、
  プロセス監視（`kill -0`/`ps`）が誤動作して長時間ジョブが即「完了」通知になることがある。長時間ジョブは
  nohup＋**ファイルベースの完了検知**（例: `find out/excel -newermt <開始時刻>`）＋Monitor ツールで待つのが確実
  （2026-07-24 スイープ運用で確立）。
