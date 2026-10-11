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

`f1` の `logs/` は緊急制動警告が多く大きい（約 5 MB）ので git には入れていない（`raw/` と `audit.*` のみ）。
再現: 対応するコミットを checkout し、`uv run python scripts/eval/run_tsk143.py --label <label> [--envs ...] [--fs ...] [--obstacle ...] [--set ...]`。
