# cav-cooperative-lc

高速道路の CAV 協調車線変更制御を SUMO/TraCI で評価するシミュレータ（修士研究の実装）。概要・実行方法は `README.md`。

- ユーザーへの連絡・報告は日本語で行う。
- **このリポジトリは公開されている。** 未発表の論文の内容・研究計画・評価の解釈はここ（コード・docs・`.claude-memory/`）に書かない。それらは作者の非公開ワークスペース（lab-workspace）に置く。

## 置かれている場所

作者の環境では非公開ワークスペース `lab-workspace` の `research/cav-cooperative-lc/` に置かれている。論文の原稿・用語集・執筆ルールはワークスペース側にある:

- 用語・記号（論文表記とコードの識別子の対応を含む）: `../../Lab/_common/glossary.md`
- 修論の決定事項・背景知識: `../../Lab/研究/2025-26/修論/context/`
- 論文執筆のスキル（paper-support, paper-check）: ワークスペースのルートの `.claude/skills/`

別の場所に clone した場合は，これらのパスは存在しない。

## 構成

- `TraCI/v2/` — 提案手法（統一調停）。`python -m v2`。v1 に依存しない自己完結パッケージ。`layer1/`（調停）・`layer2/`（実行）・`environment.py`（評価環境の形状）・`simulation.py`（毎 step のメインループ）
- `TraCI/v1/` — ベースライン（卒論手法 `custom`・LC2013 `default`・`simple`）。**凍結: 今後は使わない**
- `config/v2/<env>/` — 評価環境（diverge / merge / straight / weave / weave2）の net・rou
- `scripts/eval/` — 評価スイープ・集計・作図・Excel（`/eval-sweep` スキル，運用ルールは `docs/評価運用SOP.md`）
- `scripts/remote/` — sgnlab サーバでの実行（`/sgnlab-eval` スキル）
- `tests/golden/` — v1 の挙動不変を確かめる golden-master
- `docs/` — `spec/`（現行コードの仕様。v2 を読む・直すときはまずここ），実装計画（`実装計画_EDF統一調停_確定版.md` は設計時の仕様），評価運用 SOP

## コマンド

```bash
uv sync                                           # 依存（Python 3.13。SUMO 本体と SUMO_HOME が別途必要）
cd TraCI && uv run python -m v2 1 3400 0.5 --env merge --nogui   # 1 条件を実行
uv run python scripts/eval/run_all.py --suite proposed --quick   # 評価の動作確認（約 5 分）
uv run ruff check TraCI && uv run ruff format TraCI              # lint / format
uv run mypy                                                      # 型検査（strict）
uv run pre-commit run --all-files
uv run python tests/golden/run_golden.py check --fast            # 挙動不変の確認（軽量）
```

- フルスイープ（数百 jobs）はユーザーに確認してから回す。
- 挙動を変えないリファクタは，同じ seed で結果 CSV が一致すること（決定性）で確かめる。

## 規約

- **main に直接 push しない。** ブランチ → commit → PR。
- タスクは Notion「⚜️タスク」DB の TSK 番号で管理する（GitHub Issue は使わない）。ブランチ名は `種類/TSK-番号-要約`，コミットの件名と PR タイトルの末尾に `[TSK-番号]` を付ける。
- class 主体で書く: 操作ごとの自由関数を並べず，意味を持つ class（RSU・EDF・Safety・Snapshot・LCRequest など）に classmethod / staticmethod として振る舞いを持たせる。生成系は `from_spec` / `capture` / `build_all` のような classmethod。
- 想定外の入力は境界で検証し，期待と受け取った値を含めて `raise ValueError(...)` する。
- 評価結果の一次データ（`scripts/eval/out/raw/`，`manifest.json`）を削除・手で編集しない。
- 注意: `.gitignore` の `statistics` パターンが `simulationStatistics/statistics/` 配下のソースも無視するため，`ruff check .` がそれらを素通りする。pre-commit は明示パスで検査する。

## Claude Code の設定

- サブエージェント: `.claude/agents/`（researcher＝調査・read-only，implementer＝実装，eval-runner＝評価の実行，chore＝軽作業）
- メモリ: `.claude-memory/`（git 管理）。別の場所に clone したら `bash scripts/link-memory.sh` でリンクを張り直す。公開リポジトリなので，研究の状況や未発表の結果はメモリに書かない。
