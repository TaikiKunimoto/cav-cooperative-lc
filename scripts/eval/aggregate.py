#!/usr/bin/env python3
"""スイープ結果（manifest.json + 各 run CSV）を集計して tidy な long テーブルとサマリ表を出力する。

入力: scripts/eval/out/manifest.json（run_sweep.py が生成）
出力:
  - out/summary_long.csv      … 1 run = 1 行（メタ情報 + 主要指標）。図はこれから作る。
  - out/summary_scenario.csv  … (method, scenario) 別の集計（seed/Q/f を跨いだ平均・SD・件数）
  - out/summary_scenario.md   … 同上の Markdown 表（スライド貼付け用）
  - out/summary_robustness.csv… (method, scenario, Q, f) 別の seed 集計（図2用）

依存: pandas（pyproject に導入済み）。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR / "out"
MANIFEST = OUT_DIR / "manifest.json"

# CSV から拾う主要指標（ヘッダ名 → 出力名）。"traffic volume" は空白入りなので注意。
METRIC_COLS = {
    "simulation_time": "sim_time",
    "deadline_achievement_rate": "deadline_rate",
    "mandatory_lc_total": "mlc_total",
    "mandatory_lc_completed": "mlc_completed",
    "mandatory_lc_collided": "mlc_collided",
    "mandatory_lc_incomplete": "mlc_incomplete",
    "total_collisions": "collisions",
    "total_vehicles_involved": "collision_vehicles",
    "min_TTC": "min_ttc",
    "TET": "tet",
    "average_speed": "avg_speed",
    "average_travel_time": "avg_travel_time",
    "traffic volume": "throughput",
    "total_departed_vehicles": "departed",
    "exited_vehicles": "exited",
    "running_vehicles": "running_end",
    "canceled_vehicles": "canceled",
    "total_generated_vehicles": "generated",
}

META_KEYS = ["method", "scenario", "env", "q", "f", "seed", "obstacle", "duration_s"]

# 作動包絡（operating envelope）: 提案手法が全条件で必須LC 100% を満たす負荷域（2026-07-24 ユーザー決定 (a)）。
# 織込みは 196m/392m ゾーンの物理容量により超過需要域（weave Q>3000 / weave2 Q>3500）で過飽和となるため、
# 主結果は包絡内で提示し、超過域は「作動限界の明示」として全グリッド表に残す。
OPERATING_ENVELOPE_QMAX = {"weave": 3000, "weave2": 3500}


def _to_float(v: str | None) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _resolve_csv(job: dict) -> Path | None:
    """job の CSV パスを解決する。manifest の絶対パスが無ければ out/raw/<name>.csv へフォールバック。

    リモート（sgnlab 等）で実行した manifest は output_csv がリモートの絶対パスのため、
    回収後のローカルでは raw/ 直下の決定的名で探し直す。
    """
    csv_path = job.get("output_csv")
    if csv_path and Path(csv_path).exists():
        return Path(csv_path)
    name = job.get("name")
    if name:
        cand = OUT_DIR / "raw" / f"{name}.csv"
        if cand.exists():
            return cand
    return None


def _is_stale(job: dict) -> bool:
    """旧リポジトリ（改名前パス等）由来の manifest 遺物か。

    現行 out/ 配下を一切指さず、決定的名のフォールバックでも CSV を解決できないエントリは
    「現行スイープの run ではない」と判定して集計・excluded の双方から外す（manifest 自体は非破壊）。
    リモート実行の manifest は name フォールバックで解決できるため stale にならない。
    """
    for key in ("output_csv", "log"):
        p = job.get(key)
        if p and str(OUT_DIR) in str(p):
            return False
    return _resolve_csv(job) is None


def load_long() -> tuple[pd.DataFrame, list[dict]]:
    """manifest から (集計対象の long DataFrame, 除外された run 一覧) を返す。"""
    manifest = json.loads(MANIFEST.read_text())
    rows: list[dict] = []
    excluded: list[dict] = []
    stale = 0
    for job in manifest.get("jobs", []):
        if _is_stale(job):
            stale += 1
            continue
        if job.get("status") not in ("ok", "skipped"):
            excluded.append({"name": job.get("name"), "reason": job.get("status"), "log": job.get("log")})
            continue
        csv_path = _resolve_csv(job)
        if csv_path is None:
            excluded.append({"name": job.get("name"), "reason": "no-csv", "log": job.get("log")})
            continue
        with open(csv_path) as fh:
            data = list(csv.DictReader(fh))
        if not data:
            excluded.append({"name": job.get("name"), "reason": "empty-csv", "log": job.get("log")})
            continue
        r = data[-1]  # 1 run = 末尾 1 行
        row: dict = {k: job.get(k) for k in META_KEYS}
        for src, dst in METRIC_COLS.items():
            row[dst] = _to_float(r.get(src))
        rows.append(row)
    if stale:
        print(f"[aggregate] 旧リポジトリ由来の manifest 遺物 {stale} 件を集計対象外にしました（manifest は非破壊）")
    df = pd.DataFrame(rows)
    return df, excluded


def main() -> None:
    if not MANIFEST.exists():
        raise SystemExit(f"manifest が見つかりません: {MANIFEST}（先に run_sweep.py を実行）")
    df, excluded = load_long()

    # 除外 run は「無言で消す」と生存バイアスになる（例: 衝突でクラッシュした run が安全性集計から
    # 消える）ため、必ずファイルに残して件数を目立たせる。
    excl_path = OUT_DIR / "summary_excluded.csv"
    pd.DataFrame(excluded, columns=["name", "reason", "log"]).to_csv(excl_path, index=False)
    if excluded:
        print(f"[aggregate] ⚠ 集計から除外された run が {len(excluded)} 件あります → {excl_path}")
        print(
            "[aggregate] ⚠ 論文・スライドに数値を使う前に、除外理由（クラッシュ等）が結果を歪めないか必ず確認すること。"
        )

    if df.empty:
        raise SystemExit("集計対象の run がありません。")

    # 出口ベースのスループット [veh/h]（'traffic volume'＝departed 基準は入口通過＝供給側の指標。
    # 封鎖・渋滞で「捌けているか」を見るときはこちらを使う）
    df["exit_throughput"] = df["exited"] * 3600.0 / df["sim_time"]

    df = df.sort_values(["method", "scenario", "q", "f", "seed"]).reset_index(drop=True)
    long_path = OUT_DIR / "summary_long.csv"
    df.to_csv(long_path, index=False)
    print(f"[aggregate] long テーブル: {long_path}  ({len(df)} runs)")

    # --- (method, scenario) 別の集計（seed/Q/f 跨ぎ）。全グリッド版と作動包絡内版の両方を出す ---
    # min_TTC は車両オーバーラップ時に異常値（巨大負値）が出るため安全性は衝突件数・衝突0率で見る。
    # collisions 未計測（列が無い CSV → NaN）の run は「衝突あり」に数えず、分母から外す
    # （全 run 欠損なら NaN のまま '-' 表示。0 と表示すると「毎回衝突」という虚偽になる）。
    df["_collision_free"] = df["collisions"].eq(0).astype(float).where(df["collisions"].notna())
    df["in_envelope"] = df["q"] <= df["scenario"].map(OPERATING_ENVELOPE_QMAX).fillna(float("inf"))

    def scenario_agg(d: pd.DataFrame) -> pd.DataFrame:
        out = (
            d.groupby(["method", "scenario"])
            .agg(
                n=("seed", "size"),
                deadline_rate_mean=("deadline_rate", "mean"),
                deadline_rate_min=("deadline_rate", "min"),
                mlc_collided_sum=("mlc_collided", lambda s: s.sum(min_count=1)),
                mlc_incomplete_sum=("mlc_incomplete", lambda s: s.sum(min_count=1)),
                collisions_per_run=("collisions", "mean"),
                collision_free_pct=("_collision_free", "mean"),
                avg_speed_mean=("avg_speed", "mean"),
                throughput_mean=("throughput", "mean"),
                exit_throughput_mean=("exit_throughput", "mean"),
                canceled_mean=("canceled", "mean"),
            )
            .reset_index()
        )
        out["collision_free_pct"] *= 100.0
        return out

    def fmt(x: float | None, nd: int = 3) -> str:
        return "-" if x is None or pd.isna(x) else f"{x:.{nd}f}"

    def scenario_md(agg: pd.DataFrame) -> str:
        md_lines = [
            "| method | scenario | n | 締切達成率(平均) | 達成率(最小) | 衝突関与LC | 未完了LC | 衝突/run | 衝突0率[%] "
            "| 平均速度[m/s] | スループット[veh/h] | キャンセル(平均) |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for _, r in agg.iterrows():
            n_col = "-" if pd.isna(r["mlc_collided_sum"]) else str(int(r["mlc_collided_sum"]))
            n_inc = "-" if pd.isna(r["mlc_incomplete_sum"]) else str(int(r["mlc_incomplete_sum"]))
            md_lines.append(
                f"| {r['method']} | {r['scenario']} | {int(r['n'])} | "
                f"{fmt(r['deadline_rate_mean'])} | {fmt(r['deadline_rate_min'])} | {n_col} | {n_inc} | "
                f"{fmt(r['collisions_per_run'], 2)} | {fmt(r['collision_free_pct'], 0)} | {fmt(r['avg_speed_mean'], 2)} | "
                f"{fmt(r['throughput_mean'], 0)} | {fmt(r['canceled_mean'], 1)} |"
            )
        return "\n".join(md_lines) + "\n"

    agg = scenario_agg(df)
    scen_path = OUT_DIR / "summary_scenario.csv"
    agg.to_csv(scen_path, index=False)
    (OUT_DIR / "summary_scenario.md").write_text(scenario_md(agg))
    print(f"[aggregate] scenario サマリ（全グリッド）: {scen_path}")

    # 作動包絡内（主結果）。超過需要域（weave Q>3000 / weave2 Q>3500）を除いた提示用
    agg_env = scenario_agg(df[df["in_envelope"]])
    env_path = OUT_DIR / "summary_scenario_envelope.csv"
    agg_env.to_csv(env_path, index=False)
    env_md = (
        f"作動包絡内（weave Q≤{OPERATING_ENVELOPE_QMAX['weave']} / weave2 Q≤{OPERATING_ENVELOPE_QMAX['weave2']} / "
        "他は全グリッド）の集計。超過需要域は作動限界として全グリッド表（summary_scenario.md）を参照。\n\n"
    ) + scenario_md(agg_env)
    (OUT_DIR / "summary_scenario_envelope.md").write_text(env_md)
    print(f"[aggregate] scenario サマリ（作動包絡内）: {env_path}")

    # --- (method, scenario, Q, f) 別の seed 集計（図2 頑健性用）---
    rob = (
        df.groupby(["method", "scenario", "q", "f"])
        .agg(
            n=("seed", "size"),
            deadline_rate_mean=("deadline_rate", "mean"),
            deadline_rate_std=("deadline_rate", "std"),
            # min_count=1: 全 run が未計測（NaN）のセルを 0 件と偽らず NaN のままにする
            collisions_sum=("collisions", lambda s: s.sum(min_count=1)),
            avg_speed_mean=("avg_speed", "mean"),
            throughput_mean=("throughput", "mean"),
            exit_throughput_mean=("exit_throughput", "mean"),
            canceled_mean=("canceled", "mean"),
        )
        .reset_index()
    )
    rob_path = OUT_DIR / "summary_robustness.csv"
    rob.to_csv(rob_path, index=False)
    print(f"[aggregate] robustness サマリ: {rob_path}")

    # --- MLC 発生/成功 内訳（シナリオ×Q×f, seed 合計）: スライド表用 ---
    mdf = df[df["scenario"].isin(["diverge", "merge", "weave", "weave2"])].copy()
    mlc = (
        mdf.groupby(["scenario", "q", "f"])
        .agg(
            seeds=("seed", "size"),
            mlc_requested=("mlc_total", "sum"),
            mlc_completed=("mlc_completed", "sum"),
            mlc_collided=("mlc_collided", lambda s: s.sum(min_count=1)),
            mlc_incomplete=("mlc_incomplete", lambda s: s.sum(min_count=1)),
            in_envelope=("in_envelope", "first"),
        )
        .reset_index()
        .sort_values(["scenario", "q", "f"])
    )
    mlc["achievement_rate"] = (mlc["mlc_completed"] / mlc["mlc_requested"]).round(4)
    for c in ("mlc_requested", "mlc_completed", "mlc_collided", "mlc_incomplete"):
        mlc[c] = mlc[c].astype("Int64")
    mlc_path = OUT_DIR / "summary_mlc.csv"
    mlc.to_csv(mlc_path, index=False)
    print(f"[aggregate] MLC内訳: {mlc_path}")

    mlc_md = [
        "| シナリオ | 流入量Q | MLC比率f | seed数 | MLC発生回数 | MLC成功回数 | 衝突関与 | 未完了 | 達成率 | 包絡内 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in mlc.iterrows():
        rate = "-" if pd.isna(r["achievement_rate"]) else f"{r['achievement_rate'] * 100:.1f}%"
        n_col = "-" if pd.isna(r["mlc_collided"]) else str(int(r["mlc_collided"]))
        n_inc = "-" if pd.isna(r["mlc_incomplete"]) else str(int(r["mlc_incomplete"]))
        mlc_md.append(
            f"| {r['scenario']} | {int(r['q'])} | {r['f']} | {int(r['seeds'])} | "
            f"{int(r['mlc_requested'])} | {int(r['mlc_completed'])} | {n_col} | {n_inc} | {rate} | "
            f"{'✓' if bool(r['in_envelope']) else '×'} |"
        )
    (OUT_DIR / "summary_mlc.md").write_text("\n".join(mlc_md) + "\n")
    print(f"[aggregate] MLC内訳 Markdown: {OUT_DIR / 'summary_mlc.md'}")

    # コンソールに概観
    print("\n=== scenario サマリ ===")
    print(agg.to_string(index=False))


if __name__ == "__main__":
    main()
