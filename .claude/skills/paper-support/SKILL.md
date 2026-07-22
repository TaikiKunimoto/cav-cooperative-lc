---
name: paper-support
description: IPSJ ジャーナル論文（2026年9月締切）の執筆支援。和文推敲・英文アブストラクト・用語統一・typo チェック・実験結果の文章化に使う。
---

# 論文執筆支援（IPSJ ジャーナル・2026年9月締切）

原稿は `~/workspace/lab-workspace/tex/` 配下（LaTeX）。研究の一次情報はこのリポジトリの
`README.md`（手法概要）と `docs/実装計画_EDF統一調停_確定版.md`（アルゴリズム仕様）、
評価結果は `scripts/eval/out/`（summary_*.csv / FINDINGS.md）にある。

## 用語の対訳（この訳語で統一する）

| 日本語 | 英語 | 備考 |
|---|---|---|
| 協調車線変更 | cooperative lane change | |
| 必須車線変更 | mandatory lane change (MLC) | 初出で略語定義 |
| 締切達成率 | deadline achievement rate | 中核指標 |
| EDF統一調停 | unified EDF-based arbitration | EDF = earliest deadline first |
| 分流 / 合流 / 織込み | diverge / merge / weave | |
| 突発障害物 | sudden obstacle / lane blockage | 文脈で使い分け |
| 提供車 / 要求車 | cooperating vehicle / requesting vehicle | |
| 通信遅延 | communication delay | |

新しい用語を訳したら、この表に追記する PR を提案すること（勝手に別訳語を混在させない）。

## タスク別の作法

- **和→英翻訳**: 直訳でなく IPSJ/IEEE 論文の慣用表現へ。翻訳後に (1) 用語表との一致
  (2) 冠詞・単複 (3) 時制（手法説明は現在形、実験は過去形）をセルフチェックして根拠つきで報告。
- **typo・整合チェック**: 図表番号と本文参照の一致、記号（Q, f, δ, Tc, R）の定義済み確認、
  数値が `summary_*.csv` と一致するかの照合。**原稿の数値を勝手に書き換えない**（差分を報告）。
- **実験結果の文章化**: `scripts/eval/out/FINDINGS.md` と summary CSV を一次情報とし、
  書いた文中の全数値に出典（ファイル名）を添えて提示する。

## してはいけないこと

- 原稿ファイル（tex/）の直接上書き保存（変更は必ず diff 提示 → ユーザー承認後に適用）。
- 出典のない数値・引用の生成（誤引用は研究不正になり得る。不明な数値は「要確認」と明示）。
