---
name: v2-evaluation-2026-06
description: v2(EDF統一調停)の修論評価スイープの結果・所見・制約と、評価パイプライン/Drive投入の状況
metadata:
  node_type: memory
  type: project
  originSessionId: d8fb6144-2746-4f84-9891-2013c7de8f8b
---

2026-06-16 に実施した提案手法 v2 の評価（明日のスライド1–2枚用）。パイプラインは `scripts/eval/`（当時の実操作: `run_sweep.py`→`aggregate.py`→`make_figures.py`、PR#49の効果検証は `compare_fix.py`）。成果物は `scripts/eval/out/`（figures/・summary_*・FINDINGS.md・eval_deliverable.zip）。

**現在(2026-07)のツール**: 上記個別ステップは `run_all.py` に一気通貫化された（実行→集計→作図→Excel。`uv run python scripts/eval/run_all.py --suite proposed [--quick]`、集計以降のみは `--skip-sweep`）。PR #50（パイプライン＋評価フック＋`config/v1-fast`）と PR #52（run_all・export_excel・check_determinism・sgnlab リモート）は**マージ済み**。詳細 `scripts/eval/README.md`、運用ルール `docs/評価運用SOP.md`、リモート実行 `scripts/remote/`。スキル `eval-sweep`・`sgnlab-eval` で呼べる。

**結果（389 run）**:
- メッセージA成立：単一機構で4必須LC環境の締切達成率 平均 97.9〜99.6%（diverge/merge/weave/weave2）。図1。
- メッセージB成立：Q1500–4000 × f0.2–0.6 全域で達成率97〜100%維持。図2。
- 安全性は要注意（正直に提示）：衝突は皆無でなく**負荷とともに増加**（Q≤2000で衝突なしrun≈90–100%、Q≥3000で40–80%）。達成率は高負荷でも維持。図 fig_safety。min_TTCはオーバーラップで異常値→安全性は衝突件数で評価。

**衝突対策の試行（PR #49, branch fix/v2-insertion-safety-gap）**: `_insertion_safe_live` の非物理な割引(`g_req×Δv/MAX_SPEED`・base1.4m)を相対制動モデル `minGap+v·τ+max(0,(v_back²−v_front²)/(2|MAX_DECEL|))` に修正、定数 `INSERT_REACTION_TIME`(暫定0.2) 追加。結果は**形状依存のトレードオフ**：衝突 149→136・衝突0率70→76%・達成率0.989→0.955。diverge/merge/straightは明確改善だが **weave/weave2(短い織込み)は達成率低下**（交差衝突は別機構、保守化が滞留を招く）。τ=0.2が0.4より良。比較は `scripts/eval/compare_fix.py`、データは out/{raw_before_fix, raw_tau04, raw(=τ0.2)}。

**非自明な制約（次回ハマり防止）**:
- v1.custom(卒論)は ~19分/run（高流入）。`config/v1-fast/`（ExitLane速度を5.56→27.78に上げ人工渋滞除去）で ~12–30分/run に短縮。net長 v1=2496m vs v2=1000m で**効率の絶対比較は不可**（スループットのみ比較可）。fig3は注記付き。
- 流入生成は1グループ≈3600 veh/h上限（`random.sample(range(600),k)`）。straight(単一群)はQ≤3500に制限。多群env(diverge等)はQ4000可。
- 評価フックは環境変数 `EVAL_OUTPUT_DIR/NAME`・`EVAL_NO_PLOT`・`EVAL_SUMOCFG`（simulation_statistics.py と v1 custom/default/simple に追加）。未設定なら従来動作＝goldenテスト不変。
- v2 は Python `random` のグローバル状態依存＝乱数消費の追加/順序変更で同一seedでも結果が変わる。実装変更後は `scripts/eval/check_determinism.py` を回す。論文用スイープはクリーンなコミットで（manifest に git_dirty 記録）。

**最終状態(2026-06-17)**: PR #49（挿入安全判定の相対制動修正＋slowDownガード）**マージ済み**。その修正後コードでスイープ再取得（必須LC4環境計 衝突149→93・衝突0率80%・達成率0.973、weave 1件のみSUMO衝突処理クラッシュ残）。`fig_straight_obstacle`（障害物回避）と `summary_mlc`（シナリオ×Q×f の MLC発生/成功内訳）を追加。

**Google Drive**: フォルダ「修論評価」id=1N5d9YblkTsOJIW6NmpP-EFGY9k_Hfd_S。サブフォルダ「提案手法評価_2026-06-16」id=1uz7uKeA0vf9onqbV-HWs34pgG2itLMYd に**修正後の図4枚(fig1/fig2/fig_safety/fig_straight_obstacle)＋FINDINGS.md＋Sheet(MLC達成内訳/シナリオ別サマリ)＋生CSV**。fig3/旧Doc/Sheet/deliverableは削除済み。rclone(`~/.local/bin/rclone`,remote gdrive)で `rclone copy/copyto <src> "gdrive:" --drive-root-folder-id <id>`（CSV→Sheetは `--drive-import-formats csv`）。
- claude.ai Drive MCP は create_file でテキスト/Doc/Sheet 投入可だが、**バイナリ(PNG)のbase64インライン投入は不可**（モデルが正確に出力できない）。
- **rclone をインストール済み（`~/.local/bin/rclone`・remote名 `gdrive`・~/.config/rclone/rclone.conf に認証済み）**。図など以降のファイル投入は `rclone copy <src> "gdrive:" --drive-root-folder-id <folderID>` で私から自動実行できる（バイナリOK）。[[no-direct-push-to-main]]
