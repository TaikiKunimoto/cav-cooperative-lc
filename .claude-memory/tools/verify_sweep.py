# ruff: noqa
"""フルスイープの最終検証: 包絡内100%（衝突込み新定義）・除外run・衝突・ドレーンcap到達の確認。

新定義: 達成率 = 衝突なく締切内に完了 / 発生。CSV に mandatory_lc_collided / mandatory_lc_incomplete が
ある場合は内訳も検証する（無い旧CSVは completed/total のみ）。
作動包絡: weave Q≤3000 / weave2 Q≤3500 / 他は全グリッド（2026-07-24 ユーザー決定 (a)）。
"""

import csv
import glob
import os
import re

OUT = "/home/taiki/workspace/cav-cooperative-lc/scripts/eval/out"
ENVELOPE_QMAX = {"weave": 3000, "weave2": 3500}

envs: dict[str, dict] = {}
fails = []
env_fails = []  # 包絡内の失敗（あってはならない）
drain_capped = []
collision_runs = []
for p in sorted(glob.glob(f"{OUT}/raw/v2__*.csv")):
    name = os.path.basename(p)[:-4]
    if name.endswith("__failures"):
        continue
    m = re.match(r"v2__(\w+?)__Q(\d+)__f([\d.]+)__s(\d+)", name)
    if not m:
        continue
    env = m.group(1)
    if env == "diverge_baseline":
        continue  # baseline suite の旧データ（本ミッション対象外）
    q = int(m.group(2))
    in_env = q <= ENVELOPE_QMAX.get(env, 10**9)
    with open(p) as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        fails.append((name, "EMPTY CSV"))
        continue
    d = rows[0]
    e = envs.setdefault(
        env,
        {"n": 0, "req": 0, "comp": 0, "colv": 0, "inc": 0, "col": 0, "col_runs": 0, "cancel": 0,
         "env_n": 0, "env_req": 0, "env_comp": 0},
    )
    e["n"] += 1
    col = float(d["total_collisions"]) if d["total_collisions"] else 0
    e["col"] += col
    if col > 0:
        e["col_runs"] += 1
        collision_runs.append((name, int(col)))
    e["cancel"] += int(d["canceled_vehicles"])
    tot, comp = d.get("mandatory_lc_total"), d.get("mandatory_lc_completed")
    colv, inc = d.get("mandatory_lc_collided"), d.get("mandatory_lc_incomplete")
    if tot not in (None, ""):
        e["req"] += int(tot)
        e["comp"] += int(comp)
        if colv not in (None, ""):
            e["colv"] += int(colv)
            e["inc"] += int(inc)
        if in_env:
            e["env_n"] += 1
            e["env_req"] += int(tot)
            e["env_comp"] += int(comp)
        if int(tot) != int(comp):
            detail = f"LC fail {int(tot) - int(comp)}/{tot}"
            if colv not in (None, ""):
                detail += f" (collided={colv} incomplete={inc})"
            fails.append((name, detail))
            if in_env:
                env_fails.append((name, detail))
    # ドレーン cap 到達チェック（ログの最終 TIME が 1500 = 600+900）
    log = f"{OUT}/logs/{name}.log"
    if os.path.exists(log):
        with open(log, errors="replace") as fh:
            txt = fh.read()
        times = re.findall(r"TIME: ([0-9.]+)", txt)
        if times and float(times[-1]) >= 1500.0:
            drain_capped.append(name)

print("=== env別サマリ（新定義: 完了=衝突なし締切内） ===")
grand_req = grand_comp = env_req = env_comp = 0
for env, e in sorted(envs.items()):
    rate = f"{100 * e['comp'] / e['req']:.3f}%" if e["req"] else "-(母数0)"
    erate = f"{100 * e['env_comp'] / e['env_req']:.3f}%" if e["env_req"] else "-"
    print(
        f"{env:14s} n={e['n']:3d} 必須LC {e['comp']}/{e['req']} = {rate}  "
        f"[包絡内 n={e['env_n']:3d}: {e['env_comp']}/{e['env_req']} = {erate}]  "
        f"衝突関与LC={e['colv']} 未完了LC={e['inc']}  衝突計={e['col']:.0f} (発生run={e['col_runs']})  canceled計={e['cancel']}"
    )
    grand_req += e["req"]
    grand_comp += e["comp"]
    env_req += e["env_req"]
    env_comp += e["env_comp"]
if grand_req:
    print(f"\n総計: {grand_comp}/{grand_req} = {100 * grand_comp / grand_req:.4f}%")
    print(f"包絡内総計: {env_comp}/{env_req} = {100 * env_comp / env_req:.4f}%")

print(f"\n【包絡内】LC失敗のある run（0 であること）: {len(env_fails)}")
for f in env_fails:
    print("  ", f)
print(f"\n【全グリッド】LC失敗のある run: {len(fails)}")
for f in fails[:20]:
    print("  ", f)
print(f"\nドレーン cap(1500s) 到達 run: {len(drain_capped)}")
for n in drain_capped[:15]:
    print("  ", n)
print(f"\n衝突のある run: {len(collision_runs)} (上位)")
for n, c in sorted(collision_runs, key=lambda x: -x[1])[:10]:
    print("  ", n, c)

failures_files = sorted(glob.glob(f"{OUT}/raw/*__failures.csv"))
print(f"\n失敗個票のある run: {len(failures_files)}")
for p in failures_files[:15]:
    print("  ", os.path.basename(p))

# excluded
exc = f"{OUT}/summary_excluded.csv"
with open(exc) as fh:
    lines = [ln for ln in fh.read().strip().splitlines() if ln]
print(f"\nsummary_excluded 行数(ヘッダ除く): {len(lines) - 1}")
for ln in lines[1:6]:
    print("  ", ln)
