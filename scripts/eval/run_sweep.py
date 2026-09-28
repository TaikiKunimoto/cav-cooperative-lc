#!/usr/bin/env python3
"""評価スイープ実行ランナー（提案 v2 / ベースライン v1 を統一実行）。

役割:
  - (method, env/scenario, Q, f, seed, obstacle) の実行マトリクスを構築し、並列に SUMO 実行する。
  - 各 run は環境変数 ``EVAL_OUTPUT_DIR`` / ``EVAL_OUTPUT_NAME`` を立てて決定的な一意名の CSV を 1 ディレクトリへ
    出力させる（simulation_statistics 側のフック）。これにより並列でも衝突せず、冪等・再開可能になる。
  - 結果の所在・状態・実測時間を manifest.json に記録する（aggregate.py が読む）。

使い方（リポジトリ直下 or どこからでも可。内部で cwd=TraCI に切替えて実行）::

    uv run python scripts/eval/run_sweep.py --suite proposed --workers 6
    uv run python scripts/eval/run_sweep.py --suite baseline --workers 4
    uv run python scripts/eval/run_sweep.py --suite proposed --quick      # 小グリッドで動作確認
    uv run python scripts/eval/run_sweep.py --suite proposed --force       # 既存 CSV を無視して再実行

設計メモ:
  - v1 (custom/simple) の time-space 図出力は EVAL_NO_PLOT=1 で抑止（高速化）。
  - v1 の流入は (inflow_pass, inflow_exit) = (round(Q*(1-f)), round(Q*f)) に写像（f=分流/必須LC比率）。
  - straight は native 必須LC が無いため obstacle 指定で評価する（B シナリオ）。
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import subprocess
import time

# --- パス解決 ---------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
TRACI_DIR = REPO_ROOT / "TraCI"
OUT_DIR = SCRIPT_DIR / "out"
RAW_DIR = OUT_DIR / "raw"  # 全 run の CSV をここへ集約（EVAL_OUTPUT_DIR）
LOG_DIR = OUT_DIR / "logs"  # 各 run の stdout/stderr
MANIFEST = OUT_DIR / "manifest.json"

PER_RUN_TIMEOUT_S = 1800  # 1 run の上限（卒論 custom の高流入は長い）

# v1 ベースラインは高速化版 net（ExitLane の人工渋滞を除去）で実行する（cwd=TraCI からの相対）。
V1_FAST_SUMOCFG = "../config/v1-fast/high-way.sumocfg"

# --- 評価グリッド -----------------------------------------------------------
# native 必須LC を持つ環境（締切達成率の母数になる）。straight は別枠（obstacle）。
MLC_ENVS = ["diverge", "merge", "weave", "weave2"]

Q_FULL = [1500, 2000, 2500, 3000, 3500, 4000]
F_FULL = [0.2, 0.4, 0.6]
SEEDS_FULL = [1, 2, 3, 4, 5]

Q_QUICK = [2000, 3000]
F_QUICK = [0.4]
SEEDS_QUICK = [1, 2]

# straight + 車線封鎖（B シナリオ）。lane=1（3車線の真ん中）, pos=500（道路中央）, appear=60s
# （中央車線で停止車両が発生→後続へ回避必須LCをエスカレーション）。
STRAIGHT_OBSTACLE = "1,500,60"

# ベースライン比較（分流 D 上で交通効率を比較）。f を固定して Q を掃引。
BASELINE_F = 0.5
# v2/default は高速なので全グリッド。custom(卒論)は ~12分/run と高コストのため少数グリッドに絞る。
BASELINE_FAST_METHODS = ["v2", "default"]
BASELINE_SLOW_METHODS = ["custom"]
BASELINE_SLOW_Q = [1500, 2500, 3500]
BASELINE_SLOW_SEEDS = [1, 2]


@dataclass
class Job:
    method: str  # "v2" | "custom" | "default" | "simple"（v2 の policy 違いは "v2-none" 等のラベル）
    scenario: str  # 表示・集計ラベル（例 diverge / straight_obs / diverge[baseline]）
    env: str  # v2 の --env 名（v1 では high-way 固定なので参考値）
    q: int  # 総流入量 Q [veh/h]
    f: float  # 必須LC比率 f（v1 では分流比率に写像）
    seed: int
    obstacle: str | None = None
    policy: str | None = None  # v2 の調停ポリシー（None/"edf"=既定でフラグ省略。"none"/"off" は --policy 付与）
    # 実行後に埋まる
    output_csv: str | None = None
    status: str = "pending"  # pending|ok|skipped|failed|timeout|error
    returncode: int | None = None
    duration_s: float | None = None
    log: str | None = None

    @property
    def name(self) -> str:
        """EVAL_OUTPUT_NAME（決定的な一意名）。"""
        obs = "" if self.obstacle is None else f"__obs{self.obstacle.replace(',', '-')}"
        return f"{self.method}__{self.scenario}__Q{self.q}__f{self.f}__s{self.seed}{obs}"

    def command(self) -> list[str]:
        if self.method.startswith("v2"):
            cmd = [
                "uv",
                "run",
                "python",
                "-m",
                "v2",
                str(self.seed),
                str(self.q),
                str(self.f),
                "--env",
                self.env,
                "--nogui",
            ]
            if self.obstacle is not None:
                cmd += ["--obstacle", self.obstacle]
            if self.policy not in (None, "edf"):
                # 既定 edf はフラグ省略（現行 CLI 互換）。none/off はアブレーション実装（柱B）が受け取る
                cmd += ["--policy", str(self.policy)]
            return cmd
        # v1 系: 位置引数 (seed, inflow_pass, inflow_exit)
        inflow_exit = round(self.q * self.f)
        inflow_pass = round(self.q * (1.0 - self.f))
        return [
            "uv",
            "run",
            "python",
            "-m",
            f"v1.{self.method}",
            str(self.seed),
            str(inflow_pass),
            str(inflow_exit),
            "--nogui",
        ]


def collect_metadata() -> dict[str, object]:
    """再現性のための実行来歴（gitコミット・SUMOバージョン・ホスト・開始時刻）を集める。"""

    def _git(*args: str) -> str:
        try:
            r = subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO_ROOT, timeout=10)
            return r.stdout.strip()
        except OSError:
            return ""

    sumo_version = ""
    try:
        r = subprocess.run(["sumo", "--version"], capture_output=True, text=True, timeout=10)
        sumo_version = r.stdout.splitlines()[0] if r.stdout else ""
    except (OSError, IndexError):
        pass

    meta: dict[str, object] = {
        "git_commit": _git("rev-parse", "--short", "HEAD") or "unknown",
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        "git_dirty": bool(_git("status", "--porcelain")),
        "sumo_version": sumo_version or "unknown",
        "hostname": platform.node(),
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    # リモート実行（rsync 同期で .git が無い）では push 時に生成された来歴ファイルへフォールバック
    if meta["git_commit"] == "unknown":
        sync_meta_path = REPO_ROOT / ".sync_metadata.json"
        if sync_meta_path.exists():
            try:
                sync_meta = json.loads(sync_meta_path.read_text())
                for k in ("git_commit", "git_branch", "git_dirty"):
                    if k in sync_meta:
                        meta[k] = sync_meta[k]
                meta["git_provenance"] = (
                    f"push元 {sync_meta.get('pushed_from', '?')} ({sync_meta.get('pushed_at', '?')})"
                )
            except (OSError, json.JSONDecodeError):
                pass
    return meta


def build_jobs(suite: str, quick: bool) -> list[Job]:
    qs = Q_QUICK if quick else Q_FULL
    fs = F_QUICK if quick else F_FULL
    seeds = SEEDS_QUICK if quick else SEEDS_FULL
    jobs: list[Job] = []

    if suite in ("proposed", "all"):
        for env in MLC_ENVS:
            for q in qs:
                for f in fs:
                    for s in seeds:
                        jobs.append(Job(method="v2", scenario=env, env=env, q=q, f=f, seed=s))
        # straight + 障害物（B）。f は無視されるので代表 f のみ。
        # straight は単一グループのため流入時刻のユニーク抽出上限（≈3600 veh/h）に当たる。Q は 3500 までに制限。
        for q in [q for q in qs if q <= 3500]:
            for s in seeds:
                jobs.append(
                    Job(
                        method="v2",
                        scenario="straight_obs",
                        env="straight",
                        q=q,
                        f=0.0,
                        seed=s,
                        obstacle=STRAIGHT_OBSTACLE,
                    )
                )

    if suite in ("baseline", "all"):
        # v2 / default（高速）は全 Q × 全 seed
        for method in BASELINE_FAST_METHODS:
            for q in qs:
                for s in seeds:
                    jobs.append(
                        Job(method=method, scenario="diverge_baseline", env="diverge", q=q, f=BASELINE_F, seed=s)
                    )
        # custom（卒論・高コスト）は少数グリッド（quick 指定時はさらに縮小）
        slow_qs = Q_QUICK if quick else BASELINE_SLOW_Q
        slow_seeds = SEEDS_QUICK if quick else BASELINE_SLOW_SEEDS
        for method in BASELINE_SLOW_METHODS:
            for q in slow_qs:
                for s in slow_seeds:
                    jobs.append(
                        Job(method=method, scenario="diverge_baseline", env="diverge", q=q, f=BASELINE_F, seed=s)
                    )

    return jobs


def run_job(job: Job, force: bool) -> Job:
    expected = RAW_DIR / f"{job.name}.csv"
    log_path = LOG_DIR / f"{job.name}.log"
    job.output_csv = str(expected)
    job.log = str(log_path)

    # --force 再実行では旧 CSV を .prev へ退避する（シミュレーションは run 開始時点で
    # 出力 CSV を 'w' で開くため、退避しないと再実行が失敗した場合に旧結果まで失われる）
    if force and expected.exists():
        expected.replace(expected.with_suffix(".csv.prev"))

    # 冪等: 既存 CSV にデータ行があればスキップ（--force で無視）
    if not force and expected.exists():
        try:
            with open(expected) as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            if len(lines) >= 2:
                job.status = "skipped"
                job.returncode = 0
                return job
        except OSError:
            pass

    env = dict(os.environ)
    env["EVAL_OUTPUT_DIR"] = str(RAW_DIR)
    env["EVAL_OUTPUT_NAME"] = job.name
    env["EVAL_NO_PLOT"] = "1"  # v1 の time-space 図を抑止（高速化）
    if job.method != "v2":
        env["EVAL_SUMOCFG"] = V1_FAST_SUMOCFG  # v1 は高速化版 net で実行

    t0 = time.time()
    try:
        with open(log_path, "w") as logf:
            proc = subprocess.run(
                job.command(),
                cwd=str(TRACI_DIR),
                env=env,
                stdout=logf,
                stderr=subprocess.STDOUT,
                timeout=PER_RUN_TIMEOUT_S,
            )
        job.returncode = proc.returncode
    except subprocess.TimeoutExpired:
        job.status = "timeout"
        job.duration_s = time.time() - t0
        return job
    except Exception as e:
        job.status = "error"
        job.log = f"{log_path} ({e})"
        job.duration_s = time.time() - t0
        return job
    job.duration_s = time.time() - t0

    if job.returncode == 0 and expected.exists():
        job.status = "ok"
    else:
        job.status = "failed"
    return job


def write_manifest(results: list[Job], suite: str, quick: bool, elapsed: float) -> None:
    """実行結果を manifest.json へ書き込む（既存 manifest とマージし、過去 sweep の来歴も保持）。

    aggregate.py が読む唯一の来歴。run_sweep / run_eval の双方から呼ばれる。
    キーは obstacle 込みの正規名で統一する（旧実装は新=name/旧=fallback でキーが食い違い、
    obstacle 付き straight が二重登録されて n が倍になっていた）。
    """
    manifest: dict[str, object] = {
        "suite": suite,
        "quick": quick,
        "total_jobs": len(results),
        "elapsed_s": elapsed,
        "raw_dir": str(RAW_DIR),
        "metadata": collect_metadata(),
    }

    def _job_key(d: dict) -> str:
        obs = d.get("obstacle")
        suffix = "" if not obs else f"__obs{str(obs).replace(',', '-')}"
        return f"{d['method']}__{d['scenario']}__Q{d['q']}__f{d['f']}__s{d['seed']}{suffix}"

    existing: dict[str, dict] = {}
    if MANIFEST.exists():
        try:
            old = json.loads(MANIFEST.read_text())
            for j in old.get("jobs", []):
                existing[_job_key(j)] = j
            # 過去 sweep の来歴も残す（どのコミット・環境で採ったかの追跡用）
            history = old.get("metadata_history", [])
            if old.get("metadata"):
                history.append(old["metadata"])
            if history:
                manifest["metadata_history"] = history
        except (OSError, json.JSONDecodeError) as e:
            # 破損 manifest を黙って捨てない: 退避してから新規作成する（過去 run の来歴は
            # 退避側に残る。CSV 自体は raw/ にあるので --force なし再実行で再登録可能）
            corrupt = MANIFEST.with_name(f"manifest.corrupt.{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
            MANIFEST.rename(corrupt)
            print(f"[run_sweep] ⚠ 既存 manifest が読めません（{e}）。{corrupt} へ退避して作り直します。")
    for j in results:
        d = asdict(j)
        d["name"] = j.name
        existing[_job_key(d)] = d
    manifest["jobs"] = list(existing.values())
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description="評価スイープ実行ランナー")
    ap.add_argument("--suite", choices=["proposed", "baseline", "all"], default="proposed")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 2))
    ap.add_argument("--quick", action="store_true", help="小グリッドで動作確認")
    ap.add_argument("--force", action="store_true", help="既存 CSV を無視して再実行")
    ap.add_argument("--dry-run", action="store_true", help="ジョブ一覧だけ表示して終了")
    args = ap.parse_args()

    if "SUMO_HOME" not in os.environ:
        raise SystemExit("SUMO_HOME が未設定です。SUMO を有効化してから実行してください。")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    jobs = build_jobs(args.suite, args.quick)
    print(f"[run_sweep] suite={args.suite} quick={args.quick} jobs={len(jobs)} workers={args.workers}")
    by_method: dict[str, int] = {}
    for j in jobs:
        by_method[j.method] = by_method.get(j.method, 0) + 1
    print(f"[run_sweep] method内訳: {by_method}")

    if args.dry_run:
        for j in jobs:
            print(f"  {j.name}: {' '.join(j.command())}")
        return

    t_start = time.time()
    done = 0
    results: list[Job] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(run_job, j, args.force): j for j in jobs}
        for fut in as_completed(futures):
            j = fut.result()
            results.append(j)
            done += 1
            dur = f"{j.duration_s:.1f}s" if j.duration_s is not None else "-"
            print(f"[{done}/{len(jobs)}] {j.status:7s} {j.name} ({dur})")

    # manifest 出力（aggregate.py が読む）
    elapsed = time.time() - t_start
    write_manifest(results, suite=args.suite, quick=args.quick, elapsed=elapsed)

    n_ok = sum(1 for j in results if j.status in ("ok", "skipped"))
    n_bad = len(results) - n_ok
    print(f"\n[run_sweep] 完了: ok/skip={n_ok} bad={n_bad} elapsed={elapsed:.0f}s manifest={MANIFEST}")
    if n_bad:
        print("[run_sweep] 失敗/timeout の run:")
        for j in results:
            if j.status not in ("ok", "skipped"):
                print(f"  - {j.status}: {j.name}  (log: {j.log})")


if __name__ == "__main__":
    main()
