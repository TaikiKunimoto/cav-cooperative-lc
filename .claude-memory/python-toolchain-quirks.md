---
name: python-toolchain-quirks
description: uv/ruff/mypy/pre-commit は移行完了。.gitignore の statistics パターンで ruff が素通りする罠など運用上の注意点
metadata:
  node_type: memory
  type: project
---

poetry→uv 移行と後始末（ruff 0.15 / mypy 2.1 strict / pre-commit）は完了し、すべて main マージ済み（PR #2 / #4 / #5 / #6。traci・sumolib の 1.27 化も PR #17 で完了。2026-07-06 確認）。以下は今も残る運用上の注意点。

- **`.gitignore` の `statistics` パターンが広すぎる**（未対応）: `simulationStatistics/statistics/` 配下のソースまで ignore され、`ruff check .` は gitignore されたファイルを素通りする。pre-commit は明示パスで検査するのでそちらでは検出される。「lint が通った」ように見えても gitignore に飲まれていないか注意。
- **新デバイスのセットアップ手順**: `uv sync` → `uv run pre-commit install` → `bash scripts/link-memory.sh`（Claude メモリのリンク張り直し）。
- **IDE 表示の mypy エラーは古いキャッシュ由来のことがある**: CLI の `uv run mypy` の結果を正とする。
