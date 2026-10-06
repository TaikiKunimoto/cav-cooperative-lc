---
name: raise-on-unexpected-input
description: 想定外入力は黙って通さず、受け取った値つきで即 raise してデバッグを容易にする
metadata: 
  node_type: memory
  type: feedback
  originSessionId: c5d11bf6-5f18-48e1-b53c-b29cadab3ffe
---

入力パース・引数処理で予期しない値を受けたら、不明瞭な例外で落ちたり黙って通したりせず、**何が期待され何を受け取ったかを示して即 raise** する。例: `TraCI/v2/obstacle.py` の `Obstacle.from_spec` は `lane,pos,time` の要素数と数値を検証し、受け取った値つきの `ValueError` を投げる（範囲検証は `Obstacle.validate_for`）。パース/検証をクラスに寄せる構成は [[class-centric-organization]] を参照。

**Why:** 想定外入力が深い場所で cryptic に失敗するとデバッグが難しい。境界で明示的なエラーにすれば原因が即わかる。
**How to apply:** CLI引数・設定パース・外部入力の境界で形式/型/範囲を検証し、`raise ValueError(f"...期待... 受け取り: {value!r}")` のように受け取った値を含めて投げる。新規コードでも同様に心がける。
