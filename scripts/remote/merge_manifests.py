#!/usr/bin/env python3
"""リモート（sgnlab）から回収した manifest をローカル manifest.json へマージする。

run_sweep.py と同じキー（method__scenario__Q__f__seed[__obs...]）で job を突き合わせる。
同一 run が両方にある場合の優先側は --prefer で指定する（sgnlab_fetch.sh が回収モードに
合わせて渡す）:

  - ``--prefer local``（既定）: ローカルの CSV を保持した回収（--ignore-existing）に対応。
    ローカルのエントリを保持する。ただしローカルが失敗（ok/skipped 以外）でリモートが成功なら
    リモート側を採用する（失敗残骸の CSV は fetch が除去し、リモートの CSV が入っているため）。
  - ``--prefer remote``: リモートの CSV で上書きした回収（--take-remote）に対応。
    リモートのエントリを採用する（リモートが失敗でローカルが成功の場合のみローカルを残す）。

マージ前にローカル側を manifest.backup.<日時>.json として保存する（結果を失わないため）。
manifest 全体の代表来歴（metadata）は --prefer remote のときのみリモート側へ差し替え、
両方の来歴を metadata_history に残す。

使い方（sgnlab_fetch.sh から自動で呼ばれる。手動の場合はリポジトリ直下から）::

    uv run python scripts/remote/merge_manifests.py \\
        scripts/eval/out/manifest.sgnlab.json --into scripts/eval/out/manifest.json --prefer local
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path


def job_key(d: dict) -> str:
    obs = d.get("obstacle")
    suffix = "" if not obs else f"__obs{str(obs).replace(',', '-')}"
    return f"{d['method']}__{d['scenario']}__Q{d['q']}__f{d['f']}__s{d['seed']}{suffix}"


def is_ok(d: dict) -> bool:
    return d.get("status") in ("ok", "skipped")


def prefer(local: dict, remote: dict, prefer_side: str) -> dict:
    """同一 run のエントリの採用側を決める。原則は prefer_side、ただし成功が失敗に負けない。"""
    if is_ok(local) != is_ok(remote):
        return local if is_ok(local) else remote
    return remote if prefer_side == "remote" else local


def main() -> None:
    ap = argparse.ArgumentParser(description="manifest のキー単位マージ")
    ap.add_argument("source", type=Path, help="マージ元（リモートから回収した manifest）")
    ap.add_argument("--into", type=Path, required=True, help="マージ先（ローカル manifest.json）")
    ap.add_argument("--prefer", choices=["local", "remote"], default="local", help="同一 run 競合時の優先側")
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
            chosen = prefer(merged[k], j, args.prefer)
            if chosen is j:
                n_replaced += 1
            merged[k] = chosen

    dst["jobs"] = list(merged.values())
    # 来歴はローカル・リモート両方を残す。代表（metadata）の差し替えはリモート優先時のみ。
    history = dst.get("metadata_history", [])
    for m in (dst.get("metadata"), src.get("metadata")):
        if m and m not in history:
            history.append(m)
    if history:
        dst["metadata_history"] = history
    if args.prefer == "remote" and src.get("metadata"):
        dst["metadata"] = src["metadata"]
        print("[merge] 代表来歴（metadata）をリモート側に差し替えました（全来歴は metadata_history に保持）")

    args.into.write_text(json.dumps(dst, ensure_ascii=False, indent=2))
    print(f"[merge] 完了: 追加 {n_new} 件 / 置換 {n_replaced} 件 / 合計 {len(merged)} 件 -> {args.into}")


if __name__ == "__main__":
    main()
