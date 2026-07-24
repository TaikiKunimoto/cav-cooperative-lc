"""調停ポリシー（アブレーション比較の切替軸、柱B）。

提案手法（EDF）と、その効果を分離するための2つの弱化モードを1つの列挙で表す::

    EDF  … 提案手法（既定）。EDF 鍵の Phase A/B ＋ Layer2 実行。
    NONE … 優先度なし。協調ペア形成は行うが EDF 鍵を使わず、要求の活性化順（早い者勝ち）で処理し、
           提供車は単純に目標車線の最近傍後続。付け替え（displacement＝占有印・譲歩の伝播）なし。
    OFF  … 非協調。Layer1 調停・Layer2 協調減速を無効化し、車線変更・追従を SUMO 標準
           （LC2013・Krauss）に委ねる。ネット・需要・ルート・seed・締切判定は提案と完全同一。
"""

from enum import StrEnum


class Policy(StrEnum):
    """調停ポリシー。CLI の ``--policy {edf,none,off}`` から生成する（不正値は ValueError）。"""

    EDF = "edf"
    NONE = "none"
    OFF = "off"
