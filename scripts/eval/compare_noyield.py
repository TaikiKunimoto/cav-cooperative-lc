#!/usr/bin/env python3
"""TSK-128: 提案（v2）と変種（v2-noyield-pos）を、同じ条件の run どうしで並べて集計する。

両方式の生データ（run_sweep.py と同じ名前の CSV）を読み、シナリオ × Q（f・seed 通算）、シナリオ × f × Q、
シナリオごとの表を書く。本体の CSV からは必須車線変更・回避の要求数と完了数、衝突、流入できなかった台数、平均速度を、
要求個票（``__requests.csv``）からは次を数える（どちらも活性化した要求だけ）::

    held     保持の上限で速度が決まったことのある要求（hold_steps > 0）
    stopped  活性化から完了までの最低速度が YIELD_MIN_SPEED 未満の要求（min_speed < 1 m/s）
    t_p50/t_p90   活性化から完了までの時間の中央値・90%点 [s]（締切内に完了した要求）
    margin_p10/margin_p50   完了時の残距離（締切位置 − 完了位置）の 10%点・中央値 [m]

使い方（リポジトリ直下から）::

    uv run python scripts/eval/compare_noyield.py --proposed-raw <提案の raw/> --variant-raw <変種の raw/>
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import re

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR / "out" / "tsk128_noyield"
METHODS = ("v2", "v2-noyield-pos")
STOP_SPEED = 1.0  # [m/s] v2/constants.py の YIELD_MIN_SPEED と同じ
SCENARIO_ORDER = ["diverge", "merge", "straight_obs", "weave", "weave2"]

NAME_RE = re.compile(
    r"^(?P<method>v2(?:-[a-z0-9-]+)?)__(?P<scenario>[a-z0-9_]+?)__Q(?P<q>\d+)__f(?P<f>[0-9.]+)__s(?P<seed>\d+)"
    r"(?P<obs>__obs[0-9-]+)?\.csv$"
)
RUN_COLS = {  # 本体の CSV（末尾 1 行）から拾う列 → 出力名
    "mandatory_lc_total": "mlc_total",
    "mandatory_lc_completed": "mlc_completed",
    "avoidance_lc_total": "avoid_total",
    "avoidance_lc_completed": "avoid_completed",
    "total_collisions": "collisions",
    "canceled_vehicles": "canceled",
    "total_departed_vehicles": "departed",
    "average_speed": "avg_speed",
    "average_travel_time": "avg_travel_time",
    "traffic volume": "throughput",
}
KEYS = ["scenario", "q", "f", "seed"]
REQ_NUMERIC = ["activation_time", "completion_time", "deadline_pos", "completion_pos", "hold_steps", "min_speed"]


def _float(v: str | None) -> float:
    if v is None or v == "":
        return float("nan")
    try:
        return float(v)
    except ValueError:
        return float("nan")


def load(raw_dir: Path, method: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """1 方式の (run ごとの指標, 活性化した要求の個票) を返す。"""
    runs: list[dict[str, object]] = []
    reqs: list[pd.DataFrame] = []
    for path in sorted(raw_dir.glob(f"{method}__*.csv")):
        m = NAME_RE.match(path.name)
        if m is None or m["method"] != method:
            continue  # 付随ファイル（__requests.csv など）と別方式
        with open(path) as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            raise ValueError(f"データ行の無い結果 CSV: {path}")
        meta = {
            "method": method,
            "scenario": m["scenario"],
            "q": int(m["q"]),
            "f": float(m["f"]),
            "seed": int(m["seed"]),
        }
        runs.append(meta | {dst: _float(rows[-1].get(src)) for src, dst in RUN_COLS.items()})
        req_path = path.with_name(path.stem + "__requests.csv")
        if req_path.exists():
            df = pd.read_csv(req_path)
            for col in REQ_NUMERIC:  # 空欄や未完了の値を NaN にそろえる（列が文字列で読まれる run がある）
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df[df["activation_time"].notna()]
            reqs.append(df.assign(**meta))
    if not runs:
        raise ValueError(f"{raw_dir} に方式 {method} の結果 CSV がありません")
    req_df = pd.concat(reqs, ignore_index=True) if reqs else pd.DataFrame()
    return pd.DataFrame(runs), req_df


def summarize(runs: pd.DataFrame, reqs: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """``by``（method を含む）ごとに、run の合計と要求の通算の指標をまとめる。"""
    agg = runs.groupby(by).agg(
        runs=("seed", "size"),
        mlc_total=("mlc_total", "sum"),
        mlc_completed=("mlc_completed", "sum"),
        avoid_total=("avoid_total", "sum"),
        avoid_completed=("avoid_completed", "sum"),
        collisions=("collisions", "sum"),
        canceled=("canceled", "sum"),
        departed=("departed", "sum"),
        avg_speed=("avg_speed", "mean"),
        avg_travel_time=("avg_travel_time", "mean"),
    )
    agg["mlc_rate"] = 100.0 * agg["mlc_completed"] / agg["mlc_total"].where(agg["mlc_total"] > 0)
    agg["avoid_rate"] = 100.0 * agg["avoid_completed"] / agg["avoid_total"].where(agg["avoid_total"] > 0)
    if not reqs.empty:
        r = reqs.assign(
            done=reqs["completed_in_time"].astype(str) == "True",
            held=reqs["hold_steps"].fillna(0) > 0,
            stopped=reqs["min_speed"] < STOP_SPEED,
            duration=reqs["completion_time"] - reqs["activation_time"],
            margin=reqs["deadline_pos"] - reqs["completion_pos"],
        )
        done = r[r["done"]]
        g = r.groupby(by)
        req = pd.DataFrame(
            {
                "req_rows": g.size(),
                "held": g["held"].sum(),
                "stopped": g["stopped"].sum(),
            }
        )
        gd = done.groupby(by)
        req["t_p50"] = gd["duration"].quantile(0.5)
        req["t_p90"] = gd["duration"].quantile(0.9)
        req["margin_p10"] = gd["margin"].quantile(0.1)
        req["margin_p50"] = gd["margin"].quantile(0.5)
        req["held_pct"] = 100.0 * req["held"] / req["req_rows"]
        req["stopped_pct"] = 100.0 * req["stopped"] / req["req_rows"]
        agg = agg.join(req, how="left")
    out = agg.reset_index()
    out["scenario"] = pd.Categorical(out["scenario"], SCENARIO_ORDER, ordered=True)
    return out.sort_values([c for c in by if c != "method"] + ["method"]).round(3)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--proposed-raw", type=Path, required=True, help="提案（v2）の raw/")
    ap.add_argument("--variant-raw", type=Path, required=True, help="変種（v2-noyield-pos）の raw/")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    loaded = [load(args.proposed_raw, METHODS[0]), load(args.variant_raw, METHODS[1])]
    runs = pd.concat([r for r, _ in loaded], ignore_index=True)
    reqs = pd.concat([q for _, q in loaded], ignore_index=True)

    # 比べるのは両方式がそろった条件だけ（片方にしかない run は数えて知らせる）
    have = runs.groupby(KEYS)["method"].nunique()
    paired = have[have == len(METHODS)].index
    unpaired = len(have) - len(paired)
    runs = runs.set_index(KEYS).loc[paired].reset_index()
    if not reqs.empty:
        reqs = reqs.set_index(KEYS).loc[lambda d: d.index.isin(paired)].reset_index()
    print(f"[compare] 両方式がそろった条件 {len(paired)}、片方だけの条件 {unpaired}")

    args.out.mkdir(parents=True, exist_ok=True)
    runs.sort_values([*KEYS, "method"]).to_csv(args.out / "runs.csv", index=False)
    tables = {
        "scenario.csv": ["scenario", "method"],
        "scenario_q.csv": ["scenario", "q", "method"],
        "scenario_f_q.csv": ["scenario", "f", "q", "method"],
    }
    for name, by in tables.items():
        summarize(runs, reqs, by).to_csv(args.out / name, index=False)
        print(f"[compare] {args.out / name}")


if __name__ == "__main__":
    main()
