# TSK-143 スモーク（修正の前後比較）

`scripts/eval/run_tsk143.py --label <label>` の出力。ラベルごとに `raw/`（結果 CSV・個票）、`logs/`（stdout+stderr）、
`run_manifest.txt`（起動コマンド・git コミット）、`audit.md` / `audit.csv`（`audit_runs.py --baseline out/tsk125` の出力）を置く。
修正前の比較元は `out/tsk125/`（E4 の既定条件 `v2__<env>__Q3000__f<f>__s1`、E3 の封鎖条件 `v2__<env>_obs__...__obs1-120-60`）。

| ラベル | コミット | 内容 | run |
|---|---|---|---|
| `f1` | 9d3de5f | 緊急制動の条件と応答だけを変更（F-1） | merge / weave / weave2 × f0.4 / 0.6（Q3000, seed1） |
| `f2-4` | 0370d9c | 速度指令の調停器・相対制動の追従・加速禁止の廃止（F-2〜F-4・F-7） | 同上 |
| `f5-9` | e7815f5 | ＋ 譲りの作り直し（F-5・F-6）、保持の上限化（F-8）、整列（F-9） | 同上 |
| `f11-obs` | 19876e0 | ＋ 回避後に戻る操作（F-11）・要求の鮮度（F-12）。突発封鎖 `--obstacle 1,120,60` | weave / weave2 × f0.4 |
| `f12-tc1` | 19876e0 | 同上。調停周期 `--set TC=1.0`（修正前は例外で停止した条件） | weave2 × f0.6 |
| `f5-9-tau` | 2c0560a | ＋ 追従の時間車頭 FOLLOW_TAU = 1.0 s | merge / weave / weave2 × f0.4 / 0.6 |
| `full-8197d8e` | 8197d8e | 完全版（F-1〜F-12 マージ後の main）の本番スイート `run_sweep.py --suite proposed`（手元の Mac、6 並列）。raw は `out/raw`（git 管理外）、監査は `audit_runs.py scripts/eval/out` の出力（`audit.md` / `audit.csv` のみ） | 4 環境 × Q1500〜4000 × f0.2/0.4/0.6 × seed1〜5 ＋ 封鎖付き straight × Q1500〜3500 × seed1〜5 ＝ 385 run |
| `i14-base` | 0c472c3 | I-14 の比較元。任意の車線変更（PR #73）が入った main（6f57518）と同じコード（lab-desktop、12 並列） | merge / weave / weave2 × Q3000 / 4000 × f0.4 / 0.6（seed1）＝ 12 run |
| `i14-fix` | 655c106 | ＋ 提供車を兼ねる要求車が同じ step に自力で車線を移ったら、その提供車には譲りを出さない（I-14）。`audit.*` は `--baseline out/tsk143/i14-base` の出力。`run_manifest.txt` の dirty=True は i14-base の出力（未追跡）によるもので、コードは 655c106 のまま | 同上 |
| `full-f6880ec` | f6880ec | 評価環境の作り直しと PR #72〜#79 のあとの main の本番スイート `run_all.py --suite proposed --workers 6`（lab-desktop。決定性のチェックと quick 20 run のあとに実行し、quick の 20 run は同じコミットの結果を使った）。raw は `out/raw`（git 管理外）、監査は `audit_runs.py scripts/eval/out` の出力（`audit.md` / `audit.csv` のみ）。manifest の `git_dirty: true` は、quick が書き換えた `out/` の集計と図によるもので、コードは f6880ec のまま（未コミットの変更が無い f6880ec から 4 条件を回し直し、出力 CSV がバイト一致した） | 4 環境 × Q1500〜4000 × f0.2/0.4/0.6 × seed1〜5 ＋ 封鎖付き straight × Q1500〜3500 × seed1〜5 ＝ 385 run |

`f1` の `logs/` は緊急制動警告が多く大きい（約 5 MB）ので git には入れていない（`raw/` と `audit.*` のみ）。
再現: 対応するコミットを checkout し、`uv run python scripts/eval/run_tsk143.py --label <label> [--envs ...] [--fs ...] [--obstacle ...] [--set ...]`。
