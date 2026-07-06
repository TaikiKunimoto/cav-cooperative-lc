#!/usr/bin/env python3
"""リモート（sgnlab）から回収した manifest をローカル manifest.json へマージする。

run_sweep.py と同じキー（method__scenario__Q__f__seed[__obs...]）で job を突き合わせ、
両方に同じ run がある場合は「CSV が実在し status=ok/skipped の側」を優先する。
マージ前にローカル側を manifest.backup.<日時>.json として保存する（結果を失わないため）。

使い方::

    uv run python scripts/remote/merge_manifests.py out/manifest.sgnlab.json --into out/manifest.json
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RAW_DIR = REPO_ROOT / "scripts" / "eval" / "out" / "raw"


def job_key(d: dict) -> str:
    obs = d.get("obstacle")
    suffix = "" if not obs else f"__obs{str(obs).replace(',', '-')}"
    return f"{d['method']}__{d['scenario']}__Q{d['q']}__f{d['f']}__s{d['seed']}{suffix}"


def csv_exists_locally(d: dict) -> bool:
    name = d.get("name") or job_key(d)
    return (RAW_DIR / f"{name}.csv").exists()


def prefer(local: dict, remote: dict) -> dict:
    """同一 run の manifest エントリの優先側を決める。成功＋CSV実在 > 成功 > その他。"""

    def score(d: dict) -> int:
        s = 0
        if d.get("status") in ("ok", "skipped"):
            s += 2
        if csv_exists_locally(d):
            s += 1
        return s

    return remote if score(remote) > score(local) else local


def main() -> None:
    ap = argparse.ArgumentParser(description="manifest のキー単位マージ")
    ap.add_argument("source", type=Path, help="マージ元（リモートから回収した manifest）")
    ap.add_argument("--into", type=Path, required=True, help="マージ先（ローカル manifest.json）")
    args = ap.parse_args()

    if not args.source.exists():
        raise SystemExit(f"マージ元がありません: {args.source}")
    src = json.loads(args.source.read_text())

    if args.into.exists():
        dst = json.loads(args.into.read_text())
        backup = args.into.with_name(f"manifest.backup.{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
        backup.write_text(args.into.read_text())
        print(f"[merge] ローカル manifest をバックアップ: {backup}")
    else:
        dst = {"jobs": []}

    merged: dict[str, dict] = {}
    for j in dst.get("jobs", []):
        merged[job_key(j)] = j
    n_new, n_replaced = 0, 0
    for j in src.get("jobs", []):
        k = job_key(j)
        if k not in merged:
            merged[k] = j
            n_new += 1
        else:
            chosen = prefer(merged[k], j)
            if chosen is j:
                n_replaced += 1
            merged[k] = chosen

    dst["jobs"] = list(merged.values())
    # 来歴はローカル・リモート両方を残す
    history = dst.get("metadata_history", [])
    for m in (dst.get("metadata"), src.get("metadata")):
        if m and m not in history:
            history.append(m)
    if history:
        dst["metadata_history"] = history
    if src.get("metadata"):
        dst["metadata"] = src["metadata"]

    args.into.write_text(json.dumps(dst, ensure_ascii=False, indent=2))
    print(f"[merge] 完了: 追加 {n_new} 件 / 置換 {n_replaced} 件 / 合計 {len(merged)} 件 -> {args.into}")


if __name__ == "__main__":
    main()
