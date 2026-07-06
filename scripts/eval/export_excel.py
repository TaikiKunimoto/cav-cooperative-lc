#!/usr/bin/env python3
"""集計結果（summary_*.csv + manifest.json）を1つの Excel ブックへ書き出す。

入力: scripts/eval/out/ 配下の summary_long.csv / summary_scenario.csv /
      summary_robustness.csv / summary_mlc.csv（aggregate.py が生成）と manifest.json。
出力: out/excel/評価サマリ_<gitハッシュ>_<YYYYMMDD-HHMMSS>.xlsx（毎回新規ファイル＝既存を上書きしない）。

シート構成（固定）:
  - meta        … 実行来歴（生成日時・gitコミット・SUMOバージョン・run数・失敗run）
  - long        … 1 run = 1 行の生サマリ（summary_long.csv そのまま）
  - scenario    … (method, scenario) 別集計
  - robustness  … (method, scenario, Q, f) 別の seed 集計
  - mlc         … MLC 発生/成功の内訳（シナリオ×Q×f）

使い方::

    uv run python scripts/eval/export_excel.py            # out/ の最新集計から生成
    uv run python scripts/eval/export_excel.py --out 任意のパス.xlsx

依存: pandas / openpyxl（pyproject に導入済み）。
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
OUT_DIR = SCRIPT_DIR / "out"
EXCEL_DIR = OUT_DIR / "excel"
MANIFEST = OUT_DIR / "manifest.json"

# シート名 → 入力CSV。順序どおりにブックへ並ぶ（meta が先頭）。
SHEET_SOURCES = {
    "long": OUT_DIR / "summary_long.csv",
    "scenario": OUT_DIR / "summary_scenario.csv",
    "robustness": OUT_DIR / "summary_robustness.csv",
    "mlc": OUT_DIR / "summary_mlc.csv",
}


def _git_short_hash() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=10,
        )
        return r.stdout.strip() or "nogit"
    except OSError:
        return "nogit"


def _load_manifest_meta() -> list[tuple[str, str]]:
    """manifest.json から来歴情報を [(項目, 値)] で返す。無ければその旨を返す。"""
    if not MANIFEST.exists():
        return [("manifest", f"見つかりません: {MANIFEST}")]
    m = json.loads(MANIFEST.read_text())
    jobs = m.get("jobs", [])
    ok = sum(1 for j in jobs if j.get("status") in ("ok", "skipped"))
    bad = [j for j in jobs if j.get("status") not in ("ok", "skipped")]
    rows: list[tuple[str, str]] = [
        ("総run数(manifest)", str(len(jobs))),
        ("成功run数(ok/skipped)", str(ok)),
        ("失敗run数", str(len(bad))),
    ]
    if bad:
        rows.append(("失敗run一覧", ", ".join(str(j.get("name") or j.get("scenario")) for j in bad[:20])))
    meta = m.get("metadata")
    if isinstance(meta, dict):
        for k in ("git_commit", "git_dirty", "sumo_version", "hostname", "started_at"):
            if k in meta:
                rows.append((f"sweep {k}", str(meta[k])))
    for k in ("suite", "quick", "elapsed_s"):
        if k in m:
            rows.append((f"sweep {k}", str(m[k])))
    return rows


def build_workbook(out_path: Path) -> None:
    missing = [str(p) for p in SHEET_SOURCES.values() if not p.exists()]
    if missing:
        raise SystemExit("集計CSVが不足しています（先に aggregate.py を実行）: " + ", ".join(missing))
    if out_path.exists():
        raise SystemExit(f"出力先が既に存在します（上書きしません）: {out_path}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta_rows = [
        ("生成日時", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("gitコミット(生成時点)", _git_short_hash()),
        ("入力ディレクトリ", str(OUT_DIR)),
        *_load_manifest_meta(),
    ]
    meta_df = pd.DataFrame(meta_rows, columns=["項目", "値"])

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        meta_df.to_excel(writer, sheet_name="meta", index=False)
        for sheet, csv_path in SHEET_SOURCES.items():
            df = pd.read_csv(csv_path)
            df.to_excel(writer, sheet_name=sheet, index=False)
            ws = writer.sheets[sheet]
            ws.freeze_panes = "A2"  # ヘッダ行を固定
        # 列幅をヘッダ長+αに揃える（毎回同じ見た目になるよう決定的に計算）
        for sheet in ("meta", *SHEET_SOURCES.keys()):
            ws = writer.sheets[sheet]
            for col_cells in ws.columns:
                header = str(col_cells[0].value or "")
                body_max = max((len(str(c.value)) for c in col_cells[1:50] if c.value is not None), default=0)
                letter = col_cells[0].column_letter
                ws.column_dimensions[letter].width = min(max(len(header), body_max) + 3, 40)

    print(f"[export_excel] 出力: {out_path}")
    for sheet, csv_path in SHEET_SOURCES.items():
        n = sum(1 for _ in open(csv_path)) - 1
        print(f"  - {sheet:11s} {n:5d} 行  ({csv_path.name})")


def main() -> None:
    ap = argparse.ArgumentParser(description="集計結果を Excel ブックへ書き出す")
    ap.add_argument(
        "--out", type=Path, default=None, help="出力 xlsx パス（既定: out/excel/評価サマリ_<git>_<日時>.xlsx）"
    )
    args = ap.parse_args()

    if args.out is not None:
        out_path = args.out
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = EXCEL_DIR / f"評価サマリ_{_git_short_hash()}_{stamp}.xlsx"
    build_workbook(out_path)


if __name__ == "__main__":
    main()
