# cav-cooperative-lc

高速道路における CAV（Connected and Automated Vehicle）の**協調車線変更制御**を SUMO / TraCI で評価するシミュレータ。
修士研究「通信遅延を考慮したCAVのための協調車線変更制御手法」の実装・評価環境。

- **v1（ベースライン）** … `TraCI/v1/`：卒論提案（`custom`）／ SUMO デフォルト LC2013（`default`）／ 別バリアント（`simple`）。
- **v2（修論・新規）** … `TraCI/v2/`：複数シナリオの必須車線変更を1つの優先度機構で統一的に捌く **EDF統一調停**。

---

## リポジトリ構成

```
TraCI/
├── v1/                         ベースライン手法（卒論・LC2013）
│   ├── custom.py default.py simple.py
│   └── cav/                    CAVクラス（base_cav / custom_cav / default_cav / simple_cav / constants）
├── v2/                         EDF統一調停（自己完結パッケージ・v1 非依存）
│   ├── __main__.py             エントリ（python -m v2）
│   ├── environment.py          評価環境（形状）の定義
│   ├── constants.py priority.py rsu.py safety.py pair_executor.py snapshot.py lc_request.py v2_cav.py
│   └── simulation_state.py     毎step メインループ（2フェーズ調停）
├── status/  utils/  simulationStatistics/    v1/v2 共有インフラ
config/
├── v1/                         ベースライン用 net/rou（high-way）
└── v2/<env>/                   v2 評価環境ごと（diverge / merge / straight / weave / weave2）
```

---

## 環境構築

uv を利用して依存環境をインストールします（**Python 3.13** 必須）。

1. uv をインストール：`curl -LsSf https://astral.sh/uv/install.sh | sh`
2. Python 3.13 を用意（例：`uv python install 3.13`。`.python-version` で 3.13 指定済み）
3. `uv sync`（`.venv` を Python 3.13 で作成し依存をインストール）
4. SUMO 本体をインストールし、環境変数 `SUMO_HOME` を設定（traci/sumolib は SUMO 本体とバージョンを揃える）
5. `cmd + p` → `Open Workspace` でワークスペース内で開発

---

## 実行方法

いずれも `cd TraCI` してから実行します。結果 CSV は `TraCI/simulationStatistics/statistics/<手法>/` に出力されます。

### v1（ベースライン）

引数は順に `<seed> <inflow_pass> <inflow_exit>`（pass=直進・exit=分流の流入数 [台/h]）。

```bash
uv run python -m v1.custom  1 1700 1700   # 卒論提案
uv run python -m v1.default 1 1700 1700   # SUMO デフォルト LC2013（最弱ベースライン）
uv run python -m v1.simple  1 1700 1700
```

### v2（EDF統一調停）

```bash
uv run python -m v2 <seed> <inflow> <mlc_ratio> [--env NAME] [--obstacle lane,pos,time] [--nogui]
```

- `inflow`：総流入量 Q [台/h]、`mlc_ratio`：必須LC車の比率 f（0〜1）
- `--env`：評価環境（既定 `diverge`）
- `--obstacle lane,pos,time`：突発障害物（走行中の1台を `lane`・`pos` で停止＝障害物化、`time` で発生）
- `--nogui`：ヘッドレス実行

```bash
uv run python -m v2 1 3400 0.5 --nogui                          # 分流D・総流入3400・必須LC比率0.5
uv run python -m v2 1 3400 0.5 --env merge --nogui              # 合流M
uv run python -m v2 1 3400 0.5 --env diverge --obstacle 1,1500,80 --nogui   # 分流＋突発障害物
```

---

## 評価環境（7シナリオ）

**環境＝形状（何が起きるか）** と **障害物＝突発パラメータ（どこで・いつ）** を分離。5つの環境 × `--obstacle` で7シナリオを構成できます。

| # | シナリオ | 形状 | コマンド例 |
|---|---|---|---|
| ① | 単一分流 D | 出口ランプ | `--env diverge` |
| ② | 単一合流 M | 加速車線 | `--env merge` |
| ③ | 単一障害物 B | 中央車線封鎖 | `--env straight --obstacle 1,1500,80` |
| ④ | M+D 織込み | 補助車線 weave | `--env weave` |
| ⑤ | M+D 両側織込み | 合流下＋分流上 | `--env weave2` |
| ⑥ | M+B | 合流近傍の封鎖 | `--env merge --obstacle 2,100,20` |
| ⑦ | D+B | 分流近傍の封鎖 | `--env diverge --obstacle 1,1500,80` |

障害物は `straight` 以外（merge/diverge/weave/weave2）にも付与でき、新しい組合せも作れます。
評価パラメータ（車線数 N・要素間距離 d・負荷 ρ・必須LC比率 f・突発タイミング）を各環境に掛けて評価条件を構成します。

---

## v2 の仕組み（概要）

各車は環境が生成時に与える **`(目標レーン, 締切位置)` だけ**を持ち（SUMO の route 名に非依存）、調停は環境非依存です。毎step：

1. **観測**：全車を観測し同一スナップショット S_t を固定。
2. **Phase A（鍵計算）**：必須LC要求車の鍵 `(dist小, 待ち時間大, 縦位置〔前方優先〕, ID)` を計算。実効距離 `dist = (D − pos) − (k − 1)·R`（D=締切、k=残りLC回数、R=評価パラメータ）。ID が一意なので同点なし＝**デッドロックフリー**。
3. **Phase B（割当）**：鍵順（EDF＝最早締切順）に、目標車線の後続から「鍵劣位かつ未占有の最近傍」を提供車に確保（占有印で**横取り禁止**）。
4. **Layer2（実行）**：提供車が協調減速で gap を生成し、要求車は前後二方向の安全チェック合格で**瞬時LC**。

突発障害物は走行中の CAV を1台停止させて障害物にし、後続車に必須LC（回避）を**エスカレーション**で動的付与します（障害物は停止車両として安全判定に自動で反映）。

---

## 評価実験（スイープ・集計・図表・Excel）

評価実験は1条件ずつの手実行ではなく、`scripts/eval/` のパイプラインで回す。

```bash
uv run python scripts/eval/run_all.py --suite proposed --quick   # 動作確認（約5分）
uv run python scripts/eval/run_all.py --suite proposed           # フルスイープ→集計→図→Excel
```

- 使い方の詳細：`scripts/eval/README.md`
- sgnlab（リモート）実行：`scripts/remote/README.md`
- 運用ルール・再現性・QAチェックリスト：`docs/評価運用SOP.md`

---

## 開発

- lint / format：`uv run ruff check TraCI` ／ `uv run ruff format TraCI`
- 型チェック：`uv run mypy`（strict）
- pre-commit：`uv run pre-commit run --all-files`（ruff / mypy を local フックで実行）
- **main への直 push は禁止**。変更はブランチを切って commit → PR。

---

## GUI について（macOS の注意）

macOS Tahoe（26）以降では **XQuartz の OpenGL(GLX) 不具合**により `sumo-gui` / `netedit` が描画できません（`GLXBadContext` で異常終了。SUMO 既知issue・XQuartz 非メンテ）。
**シミュレーション自体は `--nogui` のヘッドレスで正常に動作**し CSV を出力します。GUI が必要な場合はネイティブ代替（SumoGUIMac 等）や別環境を利用してください。

### net の編集（netedit が使える環境向け）

`cd config/<v1|v2>/<env>` してから：

- `netedit <name>.net.xml` で編集。node/edge 保存：File → Save Network（`Ctrl + S`）。車両/ルート保存：File → Demand elements → Save demand elements（`Ctrl + Shift + D`）。
- `sumo-gui <name>.sumocfg` で可視化（netedit からは Edit → Open in sumo-gui（`Ctrl + T`））。
- v2 の各環境 net は node/edge/connection ファイル（`*.nod.xml` / `*.edg.xml` / `*.con.xml`）から `netconvert` で再生成できます。

### SUMO メモ

node（nod）と edge（edg）から net が作られる（netedit を使わない場合）。
