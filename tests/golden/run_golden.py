#!/usr/bin/env python3
"""
ゴールデンマスター回帰ハーネス（TraCI リファクタの挙動不変を検証する安全網）

v1: 3手法のエントリポイント(default/simple/custom)を固定パラメータでヘッドレス実行し、
決定的な出力（結果CSV・tail CSV）をスナップショットとして採取/比較する。
v2（``--suite v2``）: 5 評価環境を固定条件で実行し、出力 CSV（結果・要求個票・失敗個票）を採取/比較する。
リファクタ前に `record`、各フェーズ後に `check` して **差分ゼロ** を確認する。

使い方（リポジトリルートから）:
    uv run python tests/golden/run_golden.py record         # 基準採取（リファクタ前）
    uv run python tests/golden/run_golden.py check          # 現状と基準を比較
    uv run python tests/golden/run_golden.py record --fast  # 軽量(300/300)・クラッシュ検出用
    uv run python tests/golden/run_golden.py check  --methods simple,custom
    uv run python tests/golden/run_golden.py record --suite v2              # v2 の基準採取（5 条件を並列）
    uv run python tests/golden/run_golden.py check  --suite v2 --envs weave,weave2

判定:
  - 結果CSV / tail CSV（v2 は出力 CSV 一式）の不一致 = FAIL（挙動が変わった）
  - 正規化stdout の不一致        = WARN（情報。SUMO出力ノイズを含むため参考）

決定性の前提: 各エントリポイントは argv[1] を random.seed に渡し、SUMO config は
speedDev=0.0 で乱数なし。同 (seed, inflow) なら出力は厳密一致するはず。
"""

from __future__ import annotations

import argparse
import csv
import glob
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[2]
TRACI_DIR = REPO / "TraCI"
SNAP_DIR = Path(__file__).resolve().parent / "snapshots"

# 既定のSUMO_HOME（.pkg framework）。環境変数が優先。
DEFAULT_SUMO_HOME = "/Library/Frameworks/EclipseSUMO.framework/Versions/Current/EclipseSUMO/share/sumo"

# 全手法（congestion を起こす本番相当パラメータ）
FULL_PARAMS = (1700, 1700)
# 軽量（free-flow・クラッシュ検出のみ。協調/混雑パスは網羅しない）
FAST_PARAMS = (300, 300)
ALL_METHODS = ["default", "simple", "custom"]
DEFAULT_SEED = "42"


class V2Case(NamedTuple):
    """v2 の 1 条件（python -m v2 <seed> <Q> <f> --env <env> [--obstacle lane,pos,time]）。"""

    env: str
    seed: str
    q: int
    f: float
    obstacle: str | None = None

    @property
    def key(self) -> str:
        base = f"v2_{self.env}_seed{self.seed}_Q{self.q}_f{self.f}"
        return f"{base}_obstacle" if self.obstacle else base

    def command(self) -> list[str]:
        cmd = [sys.executable, "-m", "v2", self.seed, str(self.q), str(self.f), "--env", self.env, "--nogui"]
        return [*cmd, "--obstacle", self.obstacle] if self.obstacle else cmd


# 協調・スワップ・障害物回避の各パスを通る負荷（1 条件あたり 1〜3 分。並列に実行する）
V2_CASES = [
    V2Case("diverge", "1", 2500, 0.4),
    V2Case("merge", "1", 2500, 0.4),
    V2Case("weave", "1", 3000, 0.6),
    V2Case("weave2", "2", 3000, 0.6),  # 壁ペアのスワップ（PR #56）を通る条件
    V2Case("straight", "1", 2000, 0.0, "1,500,60"),
]
V2_ENVS = [c.env for c in V2_CASES]


def sumo_home() -> str:
    # 環境変数を優先するが、bin/sumo が無い（古いbrewパス等）なら framework 既定にフォールバック。
    for sh in (os.environ.get("SUMO_HOME"), DEFAULT_SUMO_HOME):
        if sh and (Path(sh) / "bin" / "sumo").exists():
            return sh
    sys.exit(
        "SUMO not found (no bin/sumo under $SUMO_HOME nor the framework default). "
        "Set SUMO_HOME to your SUMO share/sumo dir."
    )


def key(method: str, seed: str, p: int, e: int) -> str:
    return f"{method}_seed{seed}_p{p}_e{e}"


def normalize_stdout(text: str) -> str:
    """非決定的な行（wall-clock時刻・SUMO性能サマリ）をマスクする。"""
    out = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("Now:"):
            line = "Now: <MASKED>"
        elif line.startswith("Step #"):
            # SUMOの進捗行。タイミング部分(ms/RT/UPS/TraCI)はマスクし、
            # 決定的な車両数(vehicles TOT/ACT/BUF)は残す（挙動の細粒度フィンガープリント）。
            m = re.match(r"^(Step #\d+\.\d+) \(.*?(vehicles TOT \d+ ACT \d+ BUF \d+)\)", line)
            if m:
                line = f"{m.group(1)} (<T> {m.group(2)})"
        elif re.match(r"\s*(Duration|Real time factor|UPS|TraCI-Duration|Performance):", line):
            line = re.sub(r":.*", ": <MASKED>", line)
        out.append(line)
    return "\n".join(out) + "\n"


def result_csv(method: str) -> Path | None:
    pat = str(TRACI_DIR / "simulationStatistics" / "statistics" / method / f"{method}*.csv")
    files = [f for f in glob.glob(pat) if "tail_positions" not in os.path.basename(f)]
    if not files:
        return None
    return Path(max(files, key=os.path.getmtime))


def tail_csv(method: str, seed: str, p: int, e: int) -> Path | None:
    f = TRACI_DIR / "simulationStatistics" / "statistics" / method / f"tail_positions_pass{p}_exit{e}_seed{seed}.csv"
    return f if f.exists() else None


def run_one(method: str, seed: str, p: int, e: int, env: dict[str, str]) -> tuple[str, float, int]:
    start = time.time()
    # v1 エントリポイントは TraCI/v1/ 配下のパッケージモジュール。`-m v1.<method>` で起動する
    # （cwd=TraCI を sys.path に含め、utils/cav/simulationStatistics の絶対 import を解決する）。
    proc = subprocess.run(
        [sys.executable, "-m", f"v1.{method}", seed, str(p), str(e), "--nogui"],
        cwd=str(TRACI_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    dur = time.time() - start
    if proc.returncode != 0:
        sys.stderr.write(f"[{method}] EXIT {proc.returncode}\n{proc.stderr[-2000:]}\n")
    return proc.stdout, dur, proc.returncode


Matrix = list[tuple[str, str, int, int]]


def artifacts_for(method: str, seed: str, p: int, e: int, stdout: str) -> tuple[str, Path | None, Path | None]:
    return normalize_stdout(stdout), result_csv(method), tail_csv(method, seed, p, e)


def do_record(matrix: Matrix, env: dict[str, str]) -> int:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    failed = False
    for method, seed, p, e in matrix:
        stdout, dur, rc = run_one(method, seed, p, e, env)
        if rc != 0:
            # 異常終了した実行で good スナップショットを上書きしない（壊れた基準の混入防止）
            print(f"  FAIL {key(method, seed, p, e)} (exit {rc}) — スナップショットを更新しません")
            failed = True
            continue
        out_norm, res, tail = artifacts_for(method, seed, p, e, stdout)
        d = SNAP_DIR / key(method, seed, p, e)
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        (d / "stdout.txt").write_text(out_norm)
        for name, src in (("result.csv", res), ("tail.csv", tail)):
            if src:
                shutil.copyfile(src, d / name)
        print(f"  recorded {key(method, seed, p, e)}  ({dur:.0f}s)")
    print(f"snapshots -> {SNAP_DIR}")
    return 1 if failed else 0


def do_check(matrix: Matrix, env: dict[str, str]) -> int:
    failed = False
    for method, seed, p, e in matrix:
        d = SNAP_DIR / key(method, seed, p, e)
        if not d.exists():
            print(f"  SKIP {key(method, seed, p, e)} (no snapshot — run record first)")
            continue
        stdout, dur, rc = run_one(method, seed, p, e, env)
        if rc != 0:
            # サブプロセスが異常終了したら、古い CSV との比較で誤PASSしないよう即 FAIL とする
            print(f"  [FAIL] {key(method, seed, p, e)} (exit {rc}) — サブプロセス異常終了（stale CSV比較を回避）")
            failed = True
            continue
        out_norm, res, tail = artifacts_for(method, seed, p, e, stdout)
        problems = []
        # ハード判定: CSV
        for name, cur in (("result.csv", res), ("tail.csv", tail)):
            golden = d / name
            if golden.exists() and cur:
                if golden.read_bytes() != cur.read_bytes():
                    problems.append(f"FAIL {name} differs")
            elif golden.exists() != bool(cur):
                problems.append(f"FAIL {name} presence mismatch")
        # ソフト判定: stdout
        gold_out = d / "stdout.txt"
        if gold_out.exists() and gold_out.read_text() != out_norm:
            problems.append("WARN stdout differs (informational)")
        status = "OK" if not any(x.startswith("FAIL") for x in problems) else "FAIL"
        if status == "FAIL":
            failed = True
        print(
            f"  [{status}] {key(method, seed, p, e)} ({dur:.0f}s)"
            + ("" if not problems else "  :: " + "; ".join(problems))
        )
    if failed:
        print("\n❌ 挙動が変わっています（FAIL）。差分を確認してください。")
        return 1
    print("\n✅ golden 差分ゼロ（CSV一致）。挙動は保たれています。")
    return 0


def run_v2(case: V2Case, env: dict[str, str]) -> tuple[str, float, int, dict[str, bytes]]:
    """v2 を一時ディレクトリへ出力させて 1 回実行し、出力 CSV を {スナップショット上の名前: 中身} で返す。

    EVAL_OUTPUT_DIR / EVAL_OUTPUT_NAME で出力先と名前を固定する（並列実行しても互いに上書きしない）。
    ``<key>.csv`` → result.csv、``<key>__requests.csv`` → requests.csv のように接頭辞を外して保存する。
    """
    start = time.time()
    with tempfile.TemporaryDirectory(prefix="golden-v2-") as tmp:
        run_env = {**env, "EVAL_OUTPUT_DIR": tmp, "EVAL_OUTPUT_NAME": case.key}
        proc = subprocess.run(case.command(), cwd=str(TRACI_DIR), env=run_env, capture_output=True, text=True)
        outputs = {}
        for path in sorted(Path(tmp).glob(f"{case.key}*.csv")):
            suffix = path.stem[len(case.key) :].lstrip("_")
            outputs[f"{suffix or 'result'}.csv"] = path.read_bytes()
    dur = time.time() - start
    if proc.returncode != 0:
        sys.stderr.write(f"[{case.key}] EXIT {proc.returncode}\n{proc.stderr[-2000:]}\n")
    return proc.stdout, dur, proc.returncode, outputs


def run_v2_all(cases: list[V2Case], env: dict[str, str], jobs: int) -> list[tuple[str, float, int, dict[str, bytes]]]:
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(lambda c: run_v2(c, env), cases))


def differing_columns(golden: bytes, current: bytes) -> list[str]:
    """1 行目のデータ行で値が違う列名（結果 CSV の差分表示用）。行数・列構成が違えばその旨を返す。"""
    g_rows = list(csv.DictReader(io.StringIO(golden.decode())))
    c_rows = list(csv.DictReader(io.StringIO(current.decode())))
    if len(g_rows) != len(c_rows):
        return [f"行数 {len(g_rows)}→{len(c_rows)}"]
    if not g_rows:
        return []
    if list(g_rows[0]) != list(c_rows[0]):
        return ["列構成"]
    return [col for col in g_rows[0] if g_rows[0][col] != c_rows[0][col]]


def do_record_v2(cases: list[V2Case], env: dict[str, str], jobs: int) -> int:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    failed = False
    for case, (stdout, dur, rc, outputs) in zip(cases, run_v2_all(cases, env, jobs), strict=True):
        if rc != 0 or "result.csv" not in outputs:
            # 異常終了した実行で good スナップショットを上書きしない（壊れた基準の混入防止）
            print(f"  FAIL {case.key} (exit {rc}) — スナップショットを更新しません")
            failed = True
            continue
        d = SNAP_DIR / case.key
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        (d / "stdout.txt").write_text(normalize_stdout(stdout))
        for name, data in outputs.items():
            (d / name).write_bytes(data)
        print(f"  recorded {case.key}  ({dur:.0f}s)  files={sorted(outputs)}")
    print(f"snapshots -> {SNAP_DIR}")
    return 1 if failed else 0


def do_check_v2(cases: list[V2Case], env: dict[str, str], jobs: int) -> int:
    targets = [c for c in cases if (SNAP_DIR / c.key).exists()]
    for case in cases:
        if case not in targets:
            print(f"  SKIP {case.key} (no snapshot — run record --suite v2 first)")
    failed = False
    for case, (stdout, dur, rc, outputs) in zip(targets, run_v2_all(targets, env, jobs), strict=True):
        if rc != 0:
            print(f"  [FAIL] {case.key} (exit {rc}) — サブプロセス異常終了")
            failed = True
            continue
        d = SNAP_DIR / case.key
        golden = {p.name: p.read_bytes() for p in d.glob("*.csv")}
        problems = []
        for name in sorted(set(golden) | set(outputs)):
            if name not in golden or name not in outputs:
                problems.append(f"FAIL {name} presence mismatch")
            elif golden[name] != outputs[name]:
                detail = ", ".join(differing_columns(golden[name], outputs[name])[:8])
                problems.append(f"FAIL {name} differs" + (f" ({detail})" if detail else ""))
        gold_out = d / "stdout.txt"
        if gold_out.exists() and gold_out.read_text() != normalize_stdout(stdout):
            problems.append("WARN stdout differs (informational)")
        status = "OK" if not any(x.startswith("FAIL") for x in problems) else "FAIL"
        failed = failed or status == "FAIL"
        print(f"  [{status}] {case.key} ({dur:.0f}s)" + ("" if not problems else "  :: " + "; ".join(problems)))
    if failed:
        print("\n❌ 挙動が変わっています（FAIL）。差分を確認してください。")
        return 1
    if not targets:
        print("\n⚠️ 比較できる基準がありません。先に record --suite v2 を実行してください。")
        return 1
    print("\n✅ golden 差分ゼロ（CSV一致）。挙動は保たれています。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="TraCI golden-master regression harness")
    ap.add_argument("mode", choices=["record", "check"])
    ap.add_argument("--suite", choices=["v1", "v2"], default="v1", help="v1=凍結ベースライン（既定）/ v2=提案手法")
    ap.add_argument("--fast", action="store_true", help="free-flow 300/300（クラッシュ検出のみ・低カバレッジ）")
    ap.add_argument("--methods", default=",".join(ALL_METHODS), help="comma-separated: default,simple,custom")
    ap.add_argument("--seed", default=DEFAULT_SEED)
    ap.add_argument("--envs", default=",".join(V2_ENVS), help="v2 のみ。comma-separated: " + ",".join(V2_ENVS))
    ap.add_argument("--jobs", type=int, default=len(V2_CASES), help="v2 のみ。並列に実行する条件数")
    args = ap.parse_args()

    if args.suite == "v2":
        if args.fast:
            ap.error("--fast は v1 専用です（v2 は固定の 5 条件のみ）")
        envs = [x.strip() for x in args.envs.split(",") if x.strip()]
        unknown = sorted(set(envs) - set(V2_ENVS))
        if unknown:
            raise ValueError(f"--envs は {V2_ENVS} から選んでください（受け取った値: {unknown}）")
        if args.jobs < 1:
            raise ValueError(f"--jobs は 1 以上を指定してください（受け取った値: {args.jobs}）")

    env = {**os.environ, "SUMO_HOME": sumo_home()}
    print(f"SUMO_HOME={env['SUMO_HOME']}")

    if args.suite == "v2":
        cases = [c for c in V2_CASES if c.env in envs]
        print(f"mode={args.mode}  suite=v2  cases={[c.key for c in cases]}  jobs={args.jobs}\n")
        return do_record_v2(cases, env, args.jobs) if args.mode == "record" else do_check_v2(cases, env, args.jobs)

    p, e = FAST_PARAMS if args.fast else FULL_PARAMS
    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    matrix = [(m, args.seed, p, e) for m in methods]

    print(f"mode={args.mode}  params={p}/{e}  methods={methods}  seed={args.seed}\n")

    return do_record(matrix, env) if args.mode == "record" else do_check(matrix, env)


if __name__ == "__main__":
    raise SystemExit(main())
