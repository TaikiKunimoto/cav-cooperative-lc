---
name: eval-env-redesign
description: v2評価環境(TraCI/v2)のジオメトリ再設計プロジェクトの進行状況と規約
metadata:
  node_type: memory
  type: project
  originSessionId: be656bff-3c37-48d0-970d-084c934025c6
---

> このメモは 2026-06 の設計経緯・知見の記録。現行の環境定義は `config/v2/{diverge,merge,straight,weave,weave2}` に反映済み（PR#43でジオメトリ整形マージ済み）。以下は「なぜこの形状か」を残すための背景。

v2の評価環境(config/v2/{diverge,merge,straight,weave,weave2})を「実際の道路に近い形状」へ環境ごとに再設計中。各環境を独立PRにする。

**規約:**
- net は `.nod/.edg(/.con)` ソース＋`config/v2/<env>/build.sh` で再生成。merge/diverge/weave は `netconvert --ramps.guess` で加速/減速/補助車線を自動生成（自動命名 `*-AddedOn/OffRampEdge/Node` を MergeZone/DivergeZone 等にリネーム）。weave2 は ramps.guess が**上側オフランプ非対応**のため手動 con.xml。
- buffer（合流前/判断前の独立区間）と tail（締切後）は調停に無関係＝短く（~50m）。**OnRamp は100m**（合流車の加速距離。短すぎると deadline率が落ちる）。加速/補助/出口車線は基本**下側 lane0**（weave2 のみ出口は上側 lane3＝両側織込み）。
- 手動conで急角・短いオンランプだと WeaveStart ジャンクションが肥大化して潰れる→**オンランプは緩角**にする。
- 各環境の判断ゾーン長は競合特性で決める: merge=タイトな加速194m(最難所,deadline~0.99)、diverge=本線3車線1000mの調停区間(右端lane0へ寄せる)、weave=織込み196m、weave2=両側織込み392m(分流k=2を上側へ)。

**netedit風ジオメトリ整形(2026-06-16, branch feat/v2-env-netedit):** PR一括せず1ブランチに集約。手法:
- **ランプの湾曲は edge の `shape` 属性で**（多点 smoothstep/三次カーブ）。ただし `--ramps.guess` 環境(merge/weave)は「合流直前が直線」だと ramp lane を加速車線へ自動で寝かせ綺麗に着地させるが、合流直前に shape 点を置くと自動寄せが効かず接続が折れて速度低下警告→**合流側半分は直線・遠い半分だけ湾曲**。
- **off-ramp(ExitRamp)平行化**: `shape` で分岐直後に降下→本線と平行(水平)に。diverge は手動conなので junction が taper を吸収し内部コネクタが描く→smoothstep の taper長で「滑らかさ(最大勾配)とseed衝突」が変わる(D=20が最適,最大~31°)。本線とのゴアは中心5.0m/端1.8mで統一。
- **`connection` の `shape`(net座標直書き)は不可**: netconvert がビルド毎に座標系を再正規化するため壊れる。
- **weave2(4車線コアに2車線本線)の整列**: `spreadType=center` で本線(MainApproach/MainLane)を WeaveZone 中央lane1,2と整列(rightだと3.2m横ずれ)。但し center は **ランプが本線とほぼ平行に収束すると WeaveStart junction が破滅的に肥大化**(長さ崩壊)→ランプを `shape` で明確に降下/上昇後 **目標 lane レベル(lane0=-4.8 / lane3=+4.8)へ平行進入**させてから連結すると肥大化を回避しつつ lane と整列。この技で4ランプ同時(WeaveStart で2合流, WeaveEnd で2分流)でも警告ゼロ・全整列。
- **weave2 は上下左右対称4ランプが確定版(commit 0a2f59e)**: IN/OUT とも 1:2:1(外側1:本線2:外側1)。上下2合流(OnRampBottom→lane0/OnRampTop→lane3)→本線、本線→上下2分流(lane0→OffRampBottom/lane3→OffRampTop)。env.py は through+MLC4種(合流下→lane1/合流上→lane2/分流下→lane0/分流上→lane3, weight均等)。長さ対称。deadline 全シード1.000(極端負荷でも)。weave/diverge より頑健。
- **極端負荷 Q3000/mlc0.5 は「衝突0」保証の外**: weave は baseline から衝突あり、diverge は micro形状変更で衝突seedが移動する knife-edge。検証は標準diagonal(seed1×Q2000mlc0.3 / seed2,3×Q2500mlc0.4 / seed5×Q3000mlc0.5)で行う。
- 適用済(全て衝突0): diverge(ExitRamp~50m+平行+smooth, MainLane50m) / merge(MainApproach=OnRamp 100m統一, OnRamp湾曲) / weave(OnRamp湾曲, ExitRamp~50m平行, MainApproach100m統一, tail60m※49mは高負荷で追突) / weave2(上記整列+両ランプsmooth/平行, 長さ統一)。整形は PR#43 でマージ済。
- **GUI view設定(PR#44)**: 全環境に `<env>.view.xml`(diverge=v1スキームと同一)＋sumocfg `<gui-settings-file>`。GUI起動は `--nogui` を外すだけ。
- **weave2 対角(onramp→offramp)交差車(PR#45, branch feat/v2-weave2-crossing)**: crossing_bt(OnRampBottom lane0→対角lane3→OffRampTop) / crossing_tb(上下対称)。**機構は target_lane の複数車線跨ぎを remaining_k＋next_lane で1段ずつ処理**＝対角3車線横断も単一opで可。weight=2(対角=MLCの1/2)。標準~中負荷で deadline~0.99・衝突0だが、極端負荷 Q3000/mlc0.5 は3車線横断多数が392m織込み帯の容量を超え破綻(deadline0.59~/衝突0~4)＝標準負荷運用想定。容量増には WeaveZone延長。
- **評価運用方針(user)**: 最終評価時は environment だけ変えた別ブランチを複数用意する想定（crossingブランチ等がその一例）。いまは動作確認フェーズで標準負荷で動けばOK。

**diverge は realization A が確定版:** ramps.guessの減速車線案(#34/#40)は user に2度否定された。正解は「本線3車線(DivergeZone 1000m)を走る間に分流車を右端lane0へ必須LC＝調停の本体。deadline=DivergeStart=1000m地点。そこでlane0がExitRamp(オフランプ車線~100m)へ分岐し出口、lane1,2とthroughはMainLaneへ継続」。MainApproachは廃止し投入はDivergeZone直接。net=手動con(単純分流)。

**状況(2026-06-15):**
- マージ済み: 機構self-serve-LC #33 / merge-v1 #31 / diverge-v1 #34 / straight-v1(800m) #35 / weave #36 / weave2 #37 / **diverge-594m #40(realization Aで置換済, #42参照)**。
- 第2弾(open, base main, environment.pyの別ブロックを各々改修):
  - straight #39: 3車線・1000m。
  - **diverge #42: realization A（本線3車線1000m調停＋lane0分岐オフランプ100m）。#40を置換。deadline1.000(高負荷0.987)。**
  - merge #41: bufferとtail短縮・OnRamp100m・MergeZone194m維持。deadline0.990。
- 全環境 衝突0・キャンセル0・デッドロック0。
- 別件: PR #32（車両物理統一 MAX_ACCEL=2.6/minGap=2.8）open・未マージ。

**重要な機構知見:** v2のLCは元々「目標車線に譲るprovider(後続車)が居る時だけ」しか動けず、空車線（分流の減速車線等）へ降りられなかった。PR #33 で「十分な隙間があれば自力LC」を追加。安全判定は snapshot(mainlane_edge限定)だとフィーダーedge/内部ジャンクションからの流入車を見落とし側面衝突するため、`traci.vehicle.getNeighbors`(ジャンクション跨ぎ)に置換した。関連: [[no-direct-push-to-main]]
