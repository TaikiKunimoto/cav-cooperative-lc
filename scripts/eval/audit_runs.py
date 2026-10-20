#!/usr/bin/env python3
"""run の監査: 結果 CSV・失敗個票・run ログから、受け入れ基準の各項目を run ごとに一覧にする。

対象ディレクトリは ``raw/``（結果 CSV）と ``logs/``（stdout+stderr）を持つもの（``out/tsk125``・``out/tsk143/<label>``）。
``--baseline`` に別のディレクトリを渡すと、同じ run 名（env・Q・f・seed・接尾辞）どうしで差を併記する。

項目（列）::

    status   ok / partial（CSV にデータ行が無い＝打ち切り・失敗）/ no-log
    rate     締切達成率 = mandatory_lc_completed / mandatory_lc_total（incomplete 件数を併記）
    coll     衝突イベント数（結果 CSV total_collisions）
    stuck    終了時に未完了のまま区間内に残った要求数（個票 TIMEOUT_STUCK）
    warn     SUMO の緊急制動警告（"performs emergency braking"）の件数と、減速度の分布・最大値
    ebrake   自前の緊急制動（|MAX_DECEL| 超）の発動回数（run ログの "Speed: emergency brake events"。無ければ -）
    cancel   流入できなかった台数（canceled_vehicles）
    speed    平均速度 [m/s]
    pcuh     実流入 [pcu/h]（結果 CSV の traffic volume）

使い方（リポジトリ直下から）::

    uv run python scripts/eval/audit_runs.py scripts/eval/out/tsk143/f1 --baseline scripts/eval/out/tsk125
    uv run python scripts/eval/audit_runs.py scripts/eval/out/tsk125 --names v2__weave__Q3000__f0.4__s1
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path
import re

NAME_RE = re.compile(
    r"^(?P<method>v2(?:-[a-z0-9-]+)?)__(?P<env>[a-z0-9]+?)(?:_obs)?__Q(?P<q>\d+)__f(?P<f>[0-9.]+)__s(?P<seed>\d+)(?P<rest>.*)$"
)
WARN_RE = re.compile(r"performs emergency braking .* with decel=(?P<decel>[0-9.]+)")
EBRAKE_RE = re.compile(r"Speed: emergency brake events \(decel above \|MAX_DECEL\|\): (?P<n>\d+)")
BUCKETS = [(5.5, "≤5.5"), (10.0, "5.5–10"), (30.0, "10–30"), (100.0, "30–100"), (float("inf"), ">100")]
SIDE_SUFFIXES = ("__failures", "__requests", "__obstacle", "__avoid", "__queue", "__discretionary", "__arbitration")


@dataclass
class RunAudit:
    name: str
    env: str
    q: int
    f: float
    seed: int
    rest: str
    status: str = "ok"
    total: int | None = None
    completed: int | None = None
    incomplete: int | None = None
    collisions: int | None = None
    stuck: int | None = None
    canceled: int | None = None
    speed: float | None = None
    pcuh: float | None = None
    warn: int | None = None
    warn_max: float | None = None
    warn_buckets: dict[str, int] = field(default_factory=dict)
    ebrake: int | None = None

    @property
    def key(self) -> tuple[str, int, float, int, str]:
        return (self.env, self.q, self.f, self.seed, self.rest)

    @property
    def rate(self) -> float | None:
        if self.total:
            return (self.completed or 0) / self.total
        return None


def _to_int(v: str | None) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except ValueError:
        return None


def _to_float(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def is_result_csv(p: Path) -> bool:
    return p.suffix == ".csv" and not any(s in p.stem for s in SIDE_SUFFIXES) and NAME_RE.match(p.stem) is not None


def audit_run(csv_path: Path, log_dir: Path) -> RunAudit:
    m = NAME_RE.match(csv_path.stem)
    assert m is not None
    ra = RunAudit(
        name=csv_path.stem,
        env=m["env"],
        q=int(m["q"]),
        f=float(m["f"]),
        seed=int(m["seed"]),
        rest=m["rest"],
    )
    with open(csv_path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        ra.status = "partial"
    else:
        r = rows[-1]
        ra.total = _to_int(r.get("mandatory_lc_total"))
        ra.completed = _to_int(r.get("mandatory_lc_completed"))
        ra.incomplete = _to_int(r.get("mandatory_lc_incomplete"))
        ra.collisions = _to_int(r.get("total_collisions"))
        ra.canceled = _to_int(r.get("canceled_vehicles"))
        ra.speed = _to_float(r.get("average_speed"))
        ra.pcuh = _to_float(r.get("traffic volume"))
    failures = csv_path.with_name(csv_path.stem + "__failures.csv")
    if failures.exists():
        with open(failures, newline="") as fh:
            ra.stuck = sum(1 for row in csv.DictReader(fh) if row.get("classification") == "TIMEOUT_STUCK")
    elif rows:
        ra.stuck = 0
    log = log_dir / f"{csv_path.stem}.log"
    if log.exists():
        n = 0
        mx = 0.0
        buckets = {label: 0 for _, label in BUCKETS}
        with open(log, errors="replace") as fh:
            for line in fh:
                w = WARN_RE.search(line)
                if w:
                    d = float(w["decel"])
                    n += 1
                    mx = max(mx, d)
                    for bound, label in BUCKETS:
                        if d <= bound:
                            buckets[label] += 1
                            break
                    continue
                e = EBRAKE_RE.search(line)
                if e:
                    ra.ebrake = int(e["n"])
        ra.warn = n
        ra.warn_max = mx
        ra.warn_buckets = buckets
    else:
        ra.status = "no-log" if ra.status == "ok" else ra.status
    return ra


def audit_dir(d: Path, names: list[str] | None) -> list[RunAudit]:
    raw = d / "raw" if (d / "raw").is_dir() else d
    logs = d / "logs" if (d / "logs").is_dir() else d.parent / "logs"
    out: list[RunAudit] = []
    for p in sorted(raw.glob("*.csv")):
        if not is_result_csv(p):
            continue
        if names and p.stem not in names:
            continue
        out.append(audit_run(p, logs))
    out.sort(key=lambda r: (r.env, r.q, r.f, r.seed, r.rest))
    return out


def _fmt(v: float | int | None, spec: str = "") -> str:
    if v is None:
        return "-"
    return format(v, spec)


def _delta(cur: float | int | None, base: float | int | None, spec: str = "+.1f") -> str:
    if cur is None or base is None:
        return ""
    return f" ({format(cur - base, spec)})"


def render_markdown(runs: list[RunAudit], base: dict[tuple[str, int, float, int, str], RunAudit] | None) -> str:
    head = [
        "run",
        "status",
        "rate",
        "coll",
        "stuck",
        "warn (max m/s²)",
        "warn 分布 ≤5.5/5.5–10/10–30/30–100/>100",
        "ebrake",
        "cancel",
        "speed",
        "pcu/h",
    ]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in runs:
        b = base.get(r.key) if base else None
        rate = _fmt(r.rate, ".3f") + (f" (未完了 {r.incomplete})" if r.incomplete else "")
        if b is not None and r.rate is not None and b.rate is not None:
            rate += f" ({r.rate - b.rate:+.3f})"
        dist = "/".join(str(r.warn_buckets.get(label, 0)) for _, label in BUCKETS) if r.warn is not None else "-"
        lines.append(
            "| "
            + " | ".join(
                [
                    r.name,
                    r.status,
                    rate,
                    _fmt(r.collisions) + _delta(r.collisions, b.collisions if b else None, "+d"),
                    _fmt(r.stuck),
                    f"{_fmt(r.warn)} ({_fmt(r.warn_max, '.0f')})" + _delta(r.warn, b.warn if b else None, "+d"),
                    dist,
                    _fmt(r.ebrake),
                    _fmt(r.canceled) + _delta(r.canceled, b.canceled if b else None, "+d"),
                    _fmt(r.speed, ".1f") + _delta(r.speed, b.speed if b else None),
                    _fmt(r.pcuh, ".0f") + _delta(r.pcuh, b.pcuh if b else None, "+.0f"),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def write_csv(runs: list[RunAudit], path: Path) -> None:
    cols = [
        "run",
        "env",
        "q",
        "f",
        "seed",
        "status",
        "total",
        "completed",
        "incomplete",
        "rate",
        "collisions",
        "stuck",
        "warn",
        "warn_max",
        *[f"warn_{label}" for _, label in BUCKETS],
        "ebrake",
        "canceled",
        "speed",
        "pcuh",
    ]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in runs:
            w.writerow(
                [
                    r.name,
                    r.env,
                    r.q,
                    r.f,
                    r.seed,
                    r.status,
                    r.total,
                    r.completed,
                    r.incomplete,
                    None if r.rate is None else round(r.rate, 4),
                    r.collisions,
                    r.stuck,
                    r.warn,
                    r.warn_max,
                    *[r.warn_buckets.get(label) for _, label in BUCKETS],
                    r.ebrake,
                    r.canceled,
                    None if r.speed is None else round(r.speed, 2),
                    r.pcuh,
                ]
            )


def main() -> None:
    ap = argparse.ArgumentParser(description="run の監査（受け入れ基準の一覧）")
    ap.add_argument("dir", type=Path, help="raw/ と logs/ を持つディレクトリ")
    ap.add_argument("--baseline", type=Path, default=None, help="比較元のディレクトリ（同名 run どうしで差を併記）")
    ap.add_argument("--names", nargs="*", default=None, help="対象の run 名（省略時は全部）")
    ap.add_argument("--md", type=Path, default=None, help="markdown の出力先（既定: dir/audit.md）")
    ap.add_argument("--csv", type=Path, default=None, help="CSV の出力先（既定: dir/audit.csv）")
    args = ap.parse_args()

    runs = audit_dir(args.dir, args.names)
    if not runs:
        raise SystemExit(f"結果 CSV が見つかりません: {args.dir}")
    base = None
    if args.baseline is not None:
        base = {r.key: r for r in audit_dir(args.baseline, None)}
    md = render_markdown(runs, base)
    print(md)
    md_path = args.md or (args.dir / "audit.md")
    csv_path = args.csv or (args.dir / "audit.csv")
    md_path.write_text(md + "\n")
    write_csv(runs, csv_path)
    print(f"\n[audit] {len(runs)} run -> {md_path}, {csv_path}")


if __name__ == "__main__":
    main()
