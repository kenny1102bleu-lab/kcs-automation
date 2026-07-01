# Rationale: removeDeadTriggers — 廃止関数を指すDead-triggerの一括削除

- **日付:** 2026-06-30（第5次パッチ）
- **担当ロール:** ノア（自己修復）＋ マモル（監視所見）
- **対象ファイル:** `GAS_KCS合同会社_Backend.js`
- **関連関数:** `removeDeadTriggers`
- **マニュアル準拠:** §6.2（MTTR）／§7.1（異常系プロトコル）／§6.3（Human-in-the-loop）

## 1. 現象（マモル所見）

トリガーページで以下2件が長期エラー継続していた:

| 関数 | 導入 | エラー率 | 状態 |
|---|---|---|---|
| `autoPostSunakun` | バージョン274（固定）| **100%** | 全実行失敗 |
| `positiveNewsMonitor` | Head | **59.54%** | 過半数失敗 |

## 2. 診断

`clasp pull` で本体GASプロジェクト全10ファイルを取得後、両関数名を全ファイル横断で grep:

```
grep -nE "function (autoPostSunakun|positiveNewsMonitor)\b" *.js
→ 一件もヒットなし
```

**根本原因: 両関数は現行プロジェクトに存在しない = Dead reference。**

### 2.1 autoPostSunakun の由来

memory [[project-persona-sunakun]] より:
> 実装基盤（2026-06-28現在）: `scripts/sunakun_post.py`（Python）／ ワークフロー: `.github/workflows/03_sunakun_post.yml` ／ 投稿スケジュール（JST）: 12:00 / 19:00 / 22:00（GitHub Actions cron）／ **GAS自動タイマーは廃止済み**（記憶の`engagementTick`/`04_suna_product_scraper.gs`は古い）

トリガーは「バージョン274」に固定されており、Head からコードを削除しても効かない。**トリガー自体を削除する以外に停止手段がない**。

### 2.2 positiveNewsMonitor の由来

`scripts/gas/positive_news_monitor.gs`（kcs-automation リポジトリ内）に本体コードがあり、ファイル冒頭のセットアップ手順に明記:
```
【セットアップ】
1. このスクリプトをGASプロジェクトに貼り付け
2. setupPendingNewsSheet() を1回だけ実行（シート作成）
3. installPositiveNewsTrigger() を1回だけ実行（5分トリガー登録）
```

つまり **独立GASプロジェクトへ移設する前提**の設計。本体プロジェクト（`1DEWb...3cRv`）に一時的にインストールされていたが移設後にコードは削除されたのに、**トリガーだけが残留**した。

Head 参照なのに 59.54% (100%ではない) の理由は不明だが、Apps Script が「function not found」を graceful に扱い一部の実行を成功扱いにする挙動と推測。

## 3. 修復設計

### 3.1 なぜ「ホワイトリスト削除関数」を選んだか

代替案:
- (a) 「Headに関数定義が無いトリガーを全部削除」→ 危険。バージョン固定トリガーは Head に定義が無くても正しく動くケースがある
- (b) 社長がGAS UIから手動削除 → 確実だが再発時のロールバック（Rationale）が残らない
- (c) **明示ホワイトリスト＋Run一発（採用）** → 意思決定が明文化され、追加削除も追跡可能

### 3.2 実装

```js
function removeDeadTriggers() {
  const DEAD_HANDLERS = new Set([
    'autoPostSunakun',
    'positiveNewsMonitor'
  ]);
  // 該当ハンドラのトリガーを ScriptApp.deleteTrigger() で削除、残数を返す
}
```

- **冪等**: 削除済み後の再Runは 0件削除で終了
- **副作用最小**: 名前が完全一致するもののみ削除
- **拡張手順**: 新規Dead-triggerを追加する際は memory または docs/rationale で「廃止済み」の確定を先に行うこと（コメントで明記）

## 4. Human-in-the-loop（§6.3 準拠）

**トリガー削除は不可逆（削除後、再登録には手動 or setupXxxTrigger() 再Run必要）**。よって:

1. 本パッチではコードのみ追加、**削除は自動実行しない**
2. 社長がGASエディタから `removeDeadTriggers()` を1回Runする → 実行ログで「削除: 2件 (autoPostSunakun, positiveNewsMonitor)」を確認
3. トリガーページで両者が消え、`kcsHealthMonitor` のエラー通知が減ることを翌時までに確認

**再登録が必要になった場合のロールバック:**
- `autoPostSunakun`: ロールバックは不要（Python版が現行）。もし旧GASフローを戻すなら version 274 を確認する必要
- `positiveNewsMonitor`: 独立GASプロジェクト側のトリガーを確認。本体プロジェクトへ再インストールしたい場合は `scripts/gas/positive_news_monitor.gs` を貼り付け→`installPositiveNewsTrigger()` を Run

## 5. 検証チェックリスト

- [ ] GASエディタで `removeDeadTriggers()` を1回Run → ログに「削除: 2件」
- [ ] トリガーページで `autoPostSunakun` と `positiveNewsMonitor` の行が消える
- [ ] 次の1時間内 `kcsHealthMonitor` 実行後、エラー通知2件が消滅
- [ ] 2回目の `removeDeadTriggers()` Run → ログに「削除: 0件」（idempotent 動作確認）

## 6. 副次効果

エラー率0%化により、`kcsDailyAudit`（毎日21時）の日次サマリーが本当に注目すべきエラーだけを表示できるようになる。ノイズ削減で MTTR も向上（§6.2）。

## 7. 残課題（次パッチ以降）

- **KCS_REQUIRED_TRIGGERS の見直し** — 削除後もこの2名が「必須」に含まれていると `kcsHealthMonitor` が「欠落」検知してしまう。既に含まれていないのでOK（前もって確認済み）
- **task_177621558 の ffmpeg 修復** — もる動画パイプライン側の別問題
- **positiveNewsMonitor 独立プロジェクトの正常稼働確認** — 本当に別プロジェクトで動いているか？ scripts/gas/positive_news_monitor.gs のセットアップ済み状態を追跡できる仕組みが欲しい
