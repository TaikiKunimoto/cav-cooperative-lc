"""調停（Layer1）の 1 周期ごとの計算時間の記録（TSK-155）。

毎 Tc、状態の取りまとめ（``Snapshot.capture`` と ``LCRequest.build_all``）・鍵の計算と並べ替え（Phase A）・
提供車の割当（Phase B）にかかった壁時計の時間を、そのときの車両数・要求数・割当数と一緒に残す。
run の終わりに、1 周期ごとの表（``__arbitration.csv``）と、鍵の計算と割当を合わせた時間の平均・p99・最大
（``__arbitration_summary.csv``）を書く。

壁時計の時間は、計った機械と負荷（同時に回している run の数など）で変わる。そこで CPU の名前を一緒に残し、
golden の比較からは外す（``tests/golden/run_golden.py``）。計測は時刻を読むだけで、調停の結果は変えない。
"""

import math
import platform

from pydantic import BaseModel, Field


class ArbitrationCycle(BaseModel):
    """1 周期分の計測（時間はナノ秒の整数で持つ）。"""

    sim_time: float
    vehicles: int  # 走行中の車両数（スナップショットに載る車）
    requests: int  # 要求数（鍵を計算した要求車の数）
    assignments: int  # 割り当てた提供車の数
    build_ns: int  # 状態の取りまとめと要求の生成
    keys_ns: int  # 鍵の計算と並べ替え（Phase A）
    assign_ns: int  # 提供車の割当（Phase B）

    @property
    def arbitration_ns(self) -> int:
        """鍵の計算と割当を合わせた時間（調停 1 周期の計算時間として出す値）。"""
        return self.keys_ns + self.assign_ns


class ArbitrationTiming(BaseModel):
    """run の間の調停の計算時間を貯め、run の終わりに表と要約を返す。"""

    cycles: list[ArbitrationCycle] = Field(default_factory=list)

    def record(
        self,
        sim_time: float,
        vehicles: int,
        requests: int,
        assignments: int,
        build_ns: int,
        keys_ns: int,
        assign_ns: int,
    ) -> None:
        """1 周期分を足す。時間は ``time.perf_counter_ns`` の差（負にはならない）。"""
        if min(build_ns, keys_ns, assign_ns) < 0:
            raise ValueError(
                f"計算時間は 0 以上で渡してください（受け取った値: build={build_ns}, keys={keys_ns}, "
                f"assign={assign_ns} ns）"
            )
        self.cycles.append(
            ArbitrationCycle(
                sim_time=sim_time,
                vehicles=vehicles,
                requests=requests,
                assignments=assignments,
                build_ns=build_ns,
                keys_ns=keys_ns,
                assign_ns=assign_ns,
            )
        )

    def cycle_rows(self) -> list[dict[str, float | int]]:
        """1 周期ごとの表（``__arbitration.csv``）。時間はマイクロ秒（小数 1 桁）。"""
        return [
            {
                "time": round(c.sim_time, 1),
                "vehicles": c.vehicles,
                "requests": c.requests,
                "assignments": c.assignments,
                "build_us": round(c.build_ns / 1000, 1),
                "keys_us": round(c.keys_ns / 1000, 1),
                "assign_us": round(c.assign_ns / 1000, 1),
                "arbitration_us": round(c.arbitration_ns / 1000, 1),
            }
            for c in self.cycles
        ]

    def summary_row(self) -> dict[str, float | int | str | None]:
        """run の要約（``__arbitration_summary.csv`` の 1 行）。鍵の計算と割当を合わせた時間の平均・p99・最大。"""
        totals = sorted(c.arbitration_ns for c in self.cycles)
        n = len(totals)
        return {
            "cpu": ArbitrationTiming.cpu_name(),
            "cycles": n,
            "arbitration_mean_us": round(sum(totals) / n / 1000, 1) if n else None,
            "arbitration_p99_us": round(ArbitrationTiming.percentile(totals, 0.99) / 1000, 1) if n else None,
            "arbitration_max_us": round(totals[-1] / 1000, 1) if n else None,
            "build_mean_us": round(sum(c.build_ns for c in self.cycles) / n / 1000, 1) if n else None,
            "requests_mean": round(sum(c.requests for c in self.cycles) / n, 2) if n else None,
            "requests_max": max(c.requests for c in self.cycles) if n else None,
            "vehicles_max": max(c.vehicles for c in self.cycles) if n else None,
        }

    def summary_line(self) -> str:
        """終了時に表示する 1 行（golden の stdout 比較では値をマスクする）。"""
        s = self.summary_row()
        return (
            f"Arbitration time: keys + assignment per Tc mean {s['arbitration_mean_us']} us, "
            f"p99 {s['arbitration_p99_us']} us, max {s['arbitration_max_us']} us over {s['cycles']} cycles "
            f"(max requests {s['requests_max']}, cpu {s['cpu']})"
        )

    @staticmethod
    def percentile(sorted_values: list[int], q: float) -> float:
        """昇順に並べた値の q 分位（最近傍順位法: 小さい方から ceil(q·n) 番目）。"""
        if not sorted_values:
            raise ValueError("分位を取る値が空です")
        if not 0.0 < q <= 1.0:
            raise ValueError(f"分位は 0 < q <= 1 で指定してください（受け取った値: {q}）")
        rank = math.ceil(q * len(sorted_values))
        return float(sorted_values[rank - 1])

    @staticmethod
    def cpu_name() -> str:
        """計った機械の CPU の名前（Linux は /proc/cpuinfo の model name。取れなければ platform の値）。"""
        try:
            with open("/proc/cpuinfo") as fh:
                for line in fh:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
        return platform.processor() or platform.machine()
