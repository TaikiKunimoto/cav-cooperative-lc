#!/usr/bin/env python3
"""評価パイプラインを1コマンドで実行する（実行→集計→作図→Excel）。

各ステップは独立スクリプト（run_sweep / aggregate / make_figures / export_excel）の
薄いオーケストレータ。途中で失敗したステップがあれば、そこで止まり原因ログの場所を示す。

使い方（リポジトリ直下から）::

    uv run python scripts/eval/run_all.py --suite proposed --quick   # 動作確認（約5分/8並列）
    uv run python scripts/eval/run_all.py --suite proposed           # フルスイープ（ローカル約80分）
    uv run python scripts/eval/run_all.py --skip-sweep               # 既存 out/ から集計以降だけ再実行
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

SCRIPT_DIR = Path(__file__).resolve().parent


def _run_step(name: str, args: list[str]) -> None:
    script = SCRIPT_DIR / f"{name}.py"
    if not script.exists():
        raise SystemExit(f"ステップのスクリプトが見つかりません: {script}")
    print(f"\n===== [{name}] {' '.join(args)} =====")
    proc = subprocess.run([sys.executable, str(script), *args])
    if proc.returncode != 0:
        raise SystemExit(f"[run_all] ステップ {name} が失敗しました（exit={proc.returncode}）。ここで停止します。")


def main() -> None:
    ap = argparse.ArgumentParser(description="評価パイプライン一括実行（実行→集計→作図→Excel）")
    ap.add_argument("--suite", choices=["proposed", "baseline", "all"], default="proposed")
    ap.add_argument("--quick", action="store_true", help="小グリッドで動作確認")
    ap.add_argument("--workers", type=int, default=None, help="run_sweep の並列数（省略時は run_sweep の既定）")
    ap.add_argument("--force", action="store_true", help="既存 CSV を無視して再実行")
    ap.add_argument("--skip-sweep", action="store_true", help="実行を飛ばし、既存の out/ から集計以降のみ")
    args = ap.parse_args()

    if not args.skip_sweep:
        sweep_args = ["--suite", args.suite]
        if args.quick:
            sweep_args.append("--quick")
        if args.force:
            sweep_args.append("--force")
        if args.workers is not None:
            sweep_args += ["--workers", str(args.workers)]
        _run_step("run_sweep", sweep_args)

    _run_step("aggregate", [])
    _run_step("make_figures", [])
    _run_step("export_excel", [])

    out = SCRIPT_DIR / "out"
    print("\n[run_all] 完了。成果物:")
    print(f"  - サマリCSV/MD : {out}/summary_*.csv|md")
    print(f"  - 図           : {out}/figures/")
    print(f"  - Excel        : {out}/excel/")
    print(f"  - 生CSV/ログ   : {out}/raw/ , {out}/logs/ , manifest={out}/manifest.json")


if __name__ == "__main__":
    main()
