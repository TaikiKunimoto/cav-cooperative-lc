#!/usr/bin/env python3
"""譲りの出番を数える: 速度ログ（V2_SPEED_LOG）と要求個票（__requests.csv）から、挿入の直前に提供車が譲っていた要求の数を出す。

``python -m v2`` を ``V2_SPEED_LOG=<path>`` 付きで回すと、全車・毎 step の採用規則と提供車の譲り相手（yield_for）が
CSV に残る。本スクリプトはそれを完了した要求ごとに突き合わせ、次を数える::

    immediate     活性化から 0.3 s 以内に挿入できた要求（操作不要）
    yield_before  挿入の直前 WINDOW 秒間に、提供車がこの要求車のために譲り（yield / pass）を採用していた要求
    yield_any     活性化から挿入までのどこかで譲られた要求
    align_before  挿入の直前 WINDOW 秒間に要求車自身が整列（align）を採用していた要求
    hold_before   同じく保持（hold）を採用していた要求

「譲りが無ければ入れなかった」ことの証明ではなく（それは COOP_YIELD=0 の変種との比較で測る）、
Q・f の条件ごとに譲りがどれだけ挿入に関与しているかを見る指標。

使い方（リポジトリ直下から）::

    uv run python scripts/eval/yield_necessity.py --requests out/x__requests.csv --speedlog speedlog.csv [--window 1.0]
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class YieldNecessity:
    """1 run 分の集計結果。"""

    completed: int
    immediate: int
    yield_before: int
    yield_any: int
    align_before: int
    hold_before: int
    yield_and_align: int
    median_s: float
    p90_s: float
    max_s: float

    def as_dict(self) -> dict[str, float | int]:
        return self.__dict__.copy()

    @classmethod
    def compute(cls, requests: pd.DataFrame, speedlog: pd.DataFrame, window: float = 1.0) -> "YieldNecessity":
        done = requests[requests["completed_in_time"].astype(bool)]
        yielding = speedlog[speedlog["rule"].isin(["yield", "pass"]) & speedlog["yield_for"].notna()]
        yield_times = {k: g["time"].to_numpy() for k, g in yielding.groupby("yield_for")}
        own = speedlog[speedlog["rule"].isin(["align", "hold"])]
        # DataFrameGroupBy は .keys が属性（グループ列名）なので dict(groupby) は使えない。内包表記で辞書にする
        own_by_veh = {k: g for k, g in own.groupby("veh")}  # noqa: C416
        immediate = yield_before = yield_any = align_before = hold_before = both = 0
        durations: list[float] = []
        for _, r in done.iterrows():
            vid = str(r["veh_id"])
            t, t0 = float(r["completion_time"]), float(r["activation_time"])
            durations.append(t - t0)
            immediate += t - t0 <= 0.3
            ts = yield_times.get(vid)
            y_before = ts is not None and bool(((ts >= t - window) & (ts <= t)).any())
            yield_before += y_before
            yield_any += ts is not None and bool(((ts >= t0) & (ts <= t)).any())
            g = own_by_veh.get(vid)
            in_window = (g["time"] >= t - window) & (g["time"] <= t) if g is not None else None
            a_before = g is not None and bool((in_window & (g["rule"] == "align")).any())
            align_before += a_before
            hold_before += g is not None and bool((in_window & (g["rule"] == "hold")).any())
            both += y_before and a_before
        d = pd.Series(durations, dtype=float)
        return cls(
            completed=len(done),
            immediate=immediate,
            yield_before=yield_before,
            yield_any=yield_any,
            align_before=align_before,
            hold_before=hold_before,
            yield_and_align=both,
            median_s=float(d.median()) if len(d) else float("nan"),
            p90_s=float(d.quantile(0.9)) if len(d) else float("nan"),
            max_s=float(d.max()) if len(d) else float("nan"),
        )

    def render(self) -> str:
        n = max(self.completed, 1)
        pct = lambda k: f"{k} ({k / n:.0%})"  # noqa: E731
        return "\n".join(
            [
                f"completed requests: {self.completed}",
                f"inserted within 0.3 s of activation (no maneuver): {pct(self.immediate)}",
                f"provider yielding for the requester just before insertion: {pct(self.yield_before)}",
                f"provider yielded for the requester at any time: {pct(self.yield_any)}",
                f"requester aligning just before insertion: {pct(self.align_before)}; hold: {pct(self.hold_before)}; "
                f"yield and align both: {self.yield_and_align}",
                f"activation to insertion: median {self.median_s:.1f} s, p90 {self.p90_s:.1f} s, max {self.max_s:.1f} s",
            ]
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="譲りの出番（挿入直前に提供車が譲っていた要求の数）")
    ap.add_argument("--requests", type=Path, required=True, help="要求個票 __requests.csv")
    ap.add_argument("--speedlog", type=Path, required=True, help="V2_SPEED_LOG の CSV")
    ap.add_argument("--window", type=float, default=1.0, help="挿入の直前とみなす時間窓 [s]")
    args = ap.parse_args()
    requests = pd.read_csv(args.requests)
    speedlog = pd.read_csv(args.speedlog, dtype={"veh": str, "yield_for": str})
    if "yield_for" not in speedlog.columns:
        raise ValueError(f"速度ログに yield_for 列がありません（古い形式）: {args.speedlog}")
    print(YieldNecessity.compute(requests, speedlog, args.window).render())


if __name__ == "__main__":
    main()
