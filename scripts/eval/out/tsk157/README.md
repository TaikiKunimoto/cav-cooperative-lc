# TSK-157: 要求個票に締切手前で待った記録を足した本番スイート

| 項目 | 値 |
|---|---|
| コード | 3905105（`feat/TSK-157-保持の記録`。main 970b33d ＋ requests.csv の 5 列と `margin_analysis.py` の hold_stats.csv） |
| 実行 | lab-desktop，SUMO 1.27.0，`run_all.py --suite proposed --workers 6`，2026-10-28（2,761 s） |
| run | 4 環境 × Q1500〜4000 × f0.2/0.4/0.6 × seed1〜5 ＋ 封鎖付き straight × Q1500〜3500 × seed1〜5 ＝ 385 run（全て ok） |
| raw | `out/raw`（git 管理外） |

- 生 CSV は PR #80（`full-f6880ec`）の 385 run と一致した（CSV 1,230 個）。比べなかったのは，requests.csv で足した 5 列（hold_time・hold_pos・hold_steps・order_steps・min_speed）と，壁時計の計測（`__arbitration*.csv`，summary_long.csv の duration_s・arb_*）。このため `out/summary_*.csv` と図は PR #80 のままにしてある。
- 足した出力は `out/margin/hold_stats.csv`（`margin_analysis.py --seeds 1-5`）だけ。列の定義は `MarginReport.write_hold_stats` の docstring。
