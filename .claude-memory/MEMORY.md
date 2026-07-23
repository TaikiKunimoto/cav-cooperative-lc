# Memory Index

Claude Code の永続メモリ。このディレクトリは git 管理され、
`<config>/projects/<slug>/memory`（`<config>` は `CLAUDE_CONFIG_DIR`、未設定なら既定
`~/.claude`。例: MacBook=`~/.claude-personal` / Desktop=`~/.claude`）からシンボリックリンクで参照される。

別デバイスでセットアップする場合は `scripts/link-memory.sh` を実行する（そのマシンの
config ディレクトリを自動判定してリンクを張る）。

<!-- 1行1メモリ: - [Title](file.md) — hook。追加時は該当セクションへ -->

## 進め方・作法（feedback）

- [日本語で連絡](japanese-for-user-communication.md) — ユーザーへの問いかけ・進捗報告は日本語で
- [main直push禁止](no-direct-push-to-main.md) — 変更は必ずブランチ→commit→PR

## コーディング規約（feedback）

- [class主体の構成](class-centric-organization.md) — 操作別関数モジュールでなく意味を持つclass＋class/staticmethod
- [想定外入力は即raise](raise-on-unexpected-input.md) — 境界で検証し受け取った値つきで明示エラー

## プロジェクト方針・状態（project）

- [v1は今後不使用](v1-frozen-no-longer-used.md) — v1凍結／golden full再採取は不要（PR#32でスキップ）
- [Pythonツールチェーンの注意点](python-toolchain-quirks.md) — uv移行は完了。.gitignoreのstatisticsパターンをruffが素通りする罠に注意
- [v2評価スイープ 2026-06](v2-evaluation-2026-06.md) — 提案手法v2の修論評価（達成率97.9–99.6%/高負荷で衝突増）。当時の操作・現行run_all.py・非自明な制約・Drive投入
- [v2評価環境の設計経緯](eval-env-redesign.md) — diverge/merge/weave/weave2 のジオメトリ設計知見（netconvert/ramps.guess/shape、なぜこの形状か）
