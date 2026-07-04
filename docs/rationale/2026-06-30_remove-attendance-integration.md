# Rationale: 勤怠アプリ認識とダッシュボード接続の削除

- **日付:** 2026-06-30（第7次パッチ）
- **担当ロール:** ノア（自己修復）＋ サクラ秘書（進行管理）
- **対象ファイル:** `GAS_KCS合同会社_Backend.js`
- **温存対象:** `Code.js`（勤怠アプリ本体 = AppA_Backend）
- **社長指示:** 「勤怠アプリの認識とダッシュボードへの接続を削除してください」

## 1. 削除の範囲設計

**認識** = KCS Backend が勤怠アプリの状態を Discord / 朝礼 / スプレッドシートに"引き込む"すべての経路。
**Code.js（勤怠アプリ本体）は削除しない** — これは配達ドライバー打刻/シフト照合/月次集計を担う独立業務システムで、KCS SNS運用とは切り離した組織のインフラ。KCS Backend からの参照だけを断つ。

## 2. 削除箇所（Backend.js 全7箇所）

| 箇所 | 内容 | 対応 |
|---|---|---|
| morningBriefing のデータ取得 | `const attendance = cmdTodayAttendance(config)` | 行を削除、コメントに削除理由 |
| morningBriefing Geminiプロンプト | 「- メンバー稼働状況：${attendance}」 | セクション削除 |
| morningBriefing 簡易フォールバック | 「⏱ メンバー稼働状況」＋ attendance | セクション削除 |
| `!ヘルプ` テキスト | 「`!出勤` — 本日の出勤状況」 | 行削除 |
| `!出勤` テキストコマンド | `if (/^出勤/.test(cmd))` 分岐 | 完全削除 |
| `cmdTodayAttendance()` 関数本体 | `UrlFetchApp.fetch(attUrl)` の実装 | 関数削除、コメントに削除記録 |
| Slash Commands 側 | `/attendance` help行 + case + 登録リスト + 数値 14→13 | 4箇所を削除 |
| Slash Commands 完了通知 | 「Slash Commands 14個」→「13個」 | 数値・リストから attendance 除去 |

**温存:**
- Code.js の全内容（勤怠アプリ本体）
- 設定シート `ATTENDANCE_GAS_URL` の既存値（もはや参照されない、放置しても無害）
- Backend 冒頭 line 12 のコメント「勤怠管理アプリ(AppA_Backend.gs)とは完全に別のプロジェクトです」（アーキテクチャ由来の説明で残す価値あり）

## 3. ダッシュボード接続について

Backend.js の doPost/doGet で公開されているエンドポイントを走査:

| エンドポイント | 内容 | 勤怠との関係 |
|---|---|---|
| `getTellerDashboardData(tellerId)` | 占星術占い師の販売履歴/X投稿/売上 | 無関係（"teller"は占い師） |
| その他 | プロジェクト管理/カスタムスタッフ/実務タスク | 無関係 |

Backend 側からダッシュボードに"勤怠データを流す"経路は存在しない。したがって Backend の勤怠取込を止めた時点で、ダッシュボードへの勤怠経路は自動的に消える。

**フロントエンド側（React + Vite Firebase Hosting）** は本リポ (kcs-automation) には無いため、この commit では触れない。もしフロントに `ATTENDANCE_GAS_URL` の直接呼び出しが残っている場合は追加削除が必要。社長は該当リポの場所を教えてください。

## 4. 既存破壊リスク評価

| 変更 | 影響 | 対応 |
|---|---|---|
| morningBriefing の attendance セクション削除 | 朝礼テキストから稼働状況の一節が消える | ✅ Gemini プロンプトが自然に短くなる。品質低下なし |
| `!出勤` / `/attendance` コマンド削除 | Discord で 実行しても "❓ 不明なコマンド" 応答 | ✅ 意図通り。混乱防止のためヘルプからも同時削除 |
| `cmdTodayAttendance()` 関数削除 | 他から呼ばれていないか確認済（grep で0件） | ✅ 安全 |
| Slash Commands 総数 14→13 | 次回 `registerSlashCommands` 実行時に Discord API へ反映 | ✅ 意図通り |
| Code.js 温存 | 勤怠アプリは独立稼働継続 | ✅ 業務影響なし |

## 5. デプロイ後の確認手順

1. Discord `!ヘルプ` → 「`!出勤`」の行が消える
2. Discord `!出勤` → 「❓ 不明なコマンド」応答（無効化されたことを示す）
3. 8:00の morningBriefing → 朝礼テキストに「メンバー稼働状況」が含まれない
4. GASエディタで `registerSlashCommands` を1回Run → Discord Slash に `/attendance` が消える

## 6. ロールバック手順

1. `clasp pull`
2. 本パッチの Edit 8箇所を全て逆適用
3. `clasp push -f`

`Code.js` を触っていないため、勤怠アプリ本体はいずれの状態でも稼働継続する。

## 7. 残課題

- **フロント側（Firebase Hosting）** に勤怠エンドポイント呼び出しが残っていないか要確認。リポ URL 情報が必要
- **`ATTENDANCE_GAS_URL` 設定行** をシートから物理削除するかは社長判断（KCS Backend 側では参照されなくなったので放置でも実害なし）
