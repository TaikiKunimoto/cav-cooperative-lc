#!/usr/bin/env python3
"""v2 の決定性（同一条件・同一seed → 同一結果）を検証するスモークチェック。

同じコマンドを N 回実行し、結果CSVのデータ行がバイト一致するか比較する。
v2 は Python の `random` グローバル状態に依存しており、コード変更で乱数の消費順が
変わると同一 seed でも結果が静かに変わる（simulation.py に既知リスクとして明記）。
このチェックはその退行を実装・デバッグ中に早期検出するためのガード。

使い方（リポジトリ直下から）::

    uv run python scripts/eval/check_determinism.py                     # 既定: diverge Q1500 f0.4 seed1 ×2回
    uv run python scripts/eval/check_determinism.py --env weave --q 2000 --runs 3

所要時間の目安: 1回 ≈ 100秒（Apple Silicon ローカル）× 回数。
終了コード: 一致=0 / 不一致または実行失敗=1（CI・pre-push 組込み可能）。
"""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
import subprocess
import tempfile

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
TRACI_DIR = REPO_ROOT / "TraCI"
PER_RUN_TIMEOUT_S = 900


def run_once(idx: int, out_dir: Path, env_name: str, q: float, f: float, seed: str, obstacle: str | None) -> Path:
    """v2 を1回実行し、生成された CSV パスを返す。失敗時は SystemExit。"""
    name = f"det{idx}__{env_name}__Q{q}__f{f}__s{seed}"
    env = dict(os.environ)
    env["EVAL_OUTPUT_DIR"] = str(out_dir)
    env["EVAL_OUTPUT_NAME"] = name
    log_path = out_dir / f"{name}.log"
    cmd = ["uv", "run", "python", "-m", "v2", seed, str(q), str(f), "--env", env_name, "--nogui"]
    if obstacle is not None:
        cmd += ["--obstacle", obstacle]
    print(f"[determinism] run {idx}: {' '.join(cmd)}")
    with open(log_path, "w") as logf:
        proc = subprocess.run(
            cmd, cwd=str(TRACI_DIR), env=env, stdout=logf, stderr=subprocess.STDOUT, timeout=PER_RUN_TIMEOUT_S
        )
    csv_path = out_dir / f"{name}.csv"
    if proc.returncode != 0 or not csv_path.exists():
        raise SystemExit(f"[determinism] run {idx} が失敗しました（exit={proc.returncode}）。ログ: {log_path}")
    return csv_path


def data_rows(csv_path: Path) -> list[dict[str, str]]:
    with open(csv_path) as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f"[determinism] CSV にデータ行がありません: {csv_path}")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="v2 の同一seed再現性チェック")
    ap.add_argument("--env", default="diverge", choices=["diverge", "merge", "straight", "weave", "weave2"])
    ap.add_argument("--q", type=float, default=1500.0, help="総流入 Q [veh/h]")
    ap.add_argument("--f", type=float, default=0.4, help="必須LC比率 f (0..1)")
    ap.add_argument("--seed", default="1")
    ap.add_argument("--runs", type=int, default=2, help="実行回数（2以上）")
    ap.add_argument("--obstacle", default=None, help="lane,pos,time（任意）")
    args = ap.parse_args()
    if args.runs < 2:
        raise SystemExit(f"--runs は2以上を指定してください: {args.runs}")
    if "SUMO_HOME" not in os.environ:
        raise SystemExit("SUMO_HOME が未設定です。SUMO を有効化してから実行してください。")

    with tempfile.TemporaryDirectory(prefix="v2-determinism-") as tmp:
        out_dir = Path(tmp)
        paths = []
        for i in range(1, args.runs + 1):
            # 各回で EVAL_OUTPUT_NAME を変え、run間の上書き・スキップを防ぐ
            paths.append(run_once(i, out_dir, args.env, args.q, args.f, args.seed, args.obstacle))

        base = data_rows(paths[0])
        ok = True
        for i, p in enumerate(paths[1:], start=2):
            rows = data_rows(p)
            if rows == base:
                print(f"[determinism] run 1 と run {i}: 一致")
                continue
            ok = False
            print(f"[determinism] ✗ run 1 と run {i} が不一致:")
            for r1, r2 in zip(base, rows, strict=False):
                for col in r1:
                    if r1.get(col) != r2.get(col):
                        print(f"    {col}: {r1.get(col)!r} != {r2.get(col)!r}")
            if len(base) != len(rows):
                print(f"    行数: {len(base)} != {len(rows)}")

    if not ok:
        raise SystemExit(1)
    print(f"[determinism] OK: {args.runs} 回とも同一結果（env={args.env} Q={args.q} f={args.f} seed={args.seed}）")


if __name__ == "__main__":
    main()
