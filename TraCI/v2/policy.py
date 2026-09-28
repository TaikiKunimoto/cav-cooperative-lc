"""調停ポリシー（アブレーション比較の切替軸、柱B）。

提案手法（EDF）と、その効果を分離するための2つの弱化モードを1つの列挙で表す::

    EDF      … 提案手法（既定）。EDF 鍵の Phase A/B ＋ Layer2 実行。
    NONE     … 優先度なし。協調ペア形成は行うが EDF 鍵を使わず、要求の活性化順（早い者勝ち）で処理し、
               提供車は単純に目標車線の最近傍後続。付け替え（displacement＝占有印・譲歩の伝播）なし。
    OFF      … 非協調（事前周知）。Layer1 調停・Layer2 協調減速を無効化し、車線変更・追従を SUMO 標準
               （LC2013・Krauss）に委ねる。ネット・需要・ルート・seed・締切判定は提案と完全同一。
               ルートをスポーン時から知る LC2013 は全区間を使って事前に寄れるため、
               「協調の有無」に加えて「通知タイミング」も提案と異なる点に注意（公平比較は OFF_LATE）。
    OFF_LATE … 非協調（遅通知）。OFF と同じく SUMO 標準に委ねるが、必須LC車は活性化窓に入るまで
               車線変更を凍結し、窓進入時に初めて解禁する。提案と同じ「締切付き要求が発生してから
               行動を開始する」情報タイミングに揃えた非協調ベースライン。
"""

from enum import StrEnum


class Policy(StrEnum):
    """調停ポリシー。CLI の ``--policy {edf,none,off,off-late}`` から生成する（不正値は ValueError）。"""

    EDF = "edf"
    NONE = "none"
    OFF = "off"
    OFF_LATE = "off-late"

    @property
    def is_noncooperative(self) -> bool:
        """Layer1/Layer2・縦制御を行わず SUMO 標準（LC2013・Krauss）に委ねるポリシーか。"""
        return self in (Policy.OFF, Policy.OFF_LATE)
