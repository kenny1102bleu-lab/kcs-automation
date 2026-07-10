# グロース担当ペルソナ「ソラ」新設 (2026-07-10)

## Why
社長より「Xのフォロワーを伸ばす/運用するスタッフがいない」という指摘。既存の
`get_win_patterns`（`scripts/common/engagement_loop.py`）はすなくんの投稿生成時
（`sunakun_post.py`）にのみ勝ち/負けパターンを注入する用途で使われており、
フォロワー数そのものの推移を記録・分析する仕組みは存在しなかった。HALの投稿生成
（`hal_post.py`）にも同様の仕組みは未接続。

## 何を作ったか
- `scripts/common/follower_tracker.py`: HAL/すなくんのフォロワー数を1日1回
  `data/follower_history.json` に記録し、7日前/30日前との差分を返す。
- `scripts/growth_report.py`: フォロワー差分 + 直近投稿のエンゲージメント実データ
  （`fetch_recent_post_stats`）を、新設のグロースアナリスト「ソラ」ペルソナ
  （Claude）に渡し、勝ち/負けパターンと具体的な投稿改善指示をDiscordに送る。
  データが無い項目は「データ不足」と正直に出す（[[project-morning-briefing-redesign]]
  で発覚した「いいね0/インプレ0」虚偽表示バグの再発防止）。
- `.github/workflows/08_growth_report.yml`: 毎日 JST 23:00（HAL21:00/すなくん22:00
  投稿後）に実行。`data/follower_history.json` の更新はWF-03と同じパターンで
  自動コミット。

## X APIコストについて
2026-02-06からX APIは無料枠廃止・従量課金（user read $0.010/件、post read
$0.005/件）がデフォルトになった。本ワークフローの追加コストは概算で
フォロワー取得(2アカウント×$0.01)+投稿15件×2アカウント読み取り($0.005×30)
＝1日あたり約$0.17、月あたり約$5前後。社長の少額運用承認済み。

なお `get_win_patterns` は既にすなくんの投稿生成のたびに（1日3回）30件読み取り
しており、これは本タスクで新規に発生させたコストではなく既存の挙動。

## 今後の余地
- HALの投稿生成（`hal_post.py`）にも `get_win_patterns` を接続すれば、すなくん
  同様に自アカウントの勝ちパターンを本文生成に反映できる（未着手）。
- `daily_report.py` のジュン専務プロンプトは「フォロワー推移」を参照する前提で
  書かれているが、実際にはダミーの指示文しか渡していない（[[project-morning-briefing-redesign]]
  と同種の「参照する体裁だけで実データ未接続」問題）。本ソラのレポートを
  `daily_report.py` に統合すれば解消できるが、今回はスコープ外として独立の
  Discord投稿に留めた。
