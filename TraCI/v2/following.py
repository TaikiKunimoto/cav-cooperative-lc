"""縦方向追従（car-following）の実装方式（実行層の切替軸）。

提案手法の調停（Layer1）・挿入の安全判定・スワップ・整列（Layer2）は共通のまま、提供車・要求車の協調指令が
無いときの「ふつうの追従」を誰がどう担うかだけを切り替える::

    LEGACY   … 現行実装（既定）。speedMode 0 で SUMO の安全制御を切り、``V2CAV.control_speed`` が
               壁仮定の安全車間 G_req（``Safety.g_req``）で自前追従する。既定値では結果 CSV が従来とバイト一致。
    SUMO     … 追従を SUMO（Krauss・既定 speedMode）に委ねる。control_speed は障害物の停止以外は何もしない。
               協調のための速度上書き（提供車の譲歩・締切前保持・スロット整列）は従来どおり slowDown で出し、
               指令が途切れた車は ``setSpeed(-1)`` で制御を SUMO へ返す（``V2CAV.finish_speed_step``）。
    RELATIVE … 自前追従則を相対制動（``Safety.net_required``＝挿入判定と同じ安全定義）で作り直す。
               車間不足時の目標は前車速度（legacy の「前車速度 − 1」をやめる）、充足時は余剰車間に比例して詰める。

いずれも加速禁止（do_not_speed_up）は譲っている提供車（YIELDING）のみ。legacy は LANE_CHANGING も禁止する従来挙動。
"""

from enum import StrEnum


class Following(StrEnum):
    """縦方向追従の方式。CLI の ``--following {legacy,sumo,relative}`` から生成する（不正値は ValueError）。"""

    LEGACY = "legacy"
    SUMO = "sumo"
    RELATIVE = "relative"

    @property
    def label(self) -> str:
        """手法ラベルの接尾辞（結果ファイル名・集計の method 列）。legacy は空＝従来どおり ``v2``。"""
        return {Following.LEGACY: "", Following.SUMO: "sumo", Following.RELATIVE: "rel"}[self]
