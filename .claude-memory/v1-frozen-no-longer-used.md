---
name: v1-frozen-no-longer-used
description: v1（ベースライン手法）は今後使用しない方針。golden full の再採取・維持は不要。
metadata: 
  node_type: memory
  type: project
  originSessionId: bbfb3bd8-6d28-4ebc-a09c-f574dd599a9f
---

2026-06-16、ユーザー判断: **v1（default/simple/custom のベースライン手法）は今後使う予定がない**。

**Why:** 研究の主対象は提案手法 v2（EDF統一調停）に移行済み。v1 はベースライン比較用だが、もう v1 を回す予定がない。

**How to apply:**
- `tests/golden/`（v1 回帰スナップショット）の **full(1700/1700) 再採取は不要**。F4(物理統一)等で v1 混雑挙動が変わっても full snapshot は更新しない（PR #32 で full 再採取を意図的にスキップ）。crash 検出用の fast(300/300) は軽量なので残す程度。
- v1 側の変更（例: F4 の `v1/cav/constants.py` MAX_ACCEL=2.6）は「提案/ベースラインの物理統一」目的で入れたが、v1 回帰の維持コストはかけない。
- 新規作業で v1 を起点にした提案・golden 維持を勧めない。評価・実装は v2 中心で進める。

関連: 物理統一は [[class-centric-organization]] の v2 自己完結方針とも整合（v2 は v1 非依存）。
