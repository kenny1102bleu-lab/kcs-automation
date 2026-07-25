/**
 * Discord承認Webエンドポイント (GAS)
 *
 * Discord Bot（別系統・Render.com稼働）から「!CW承認 <job_id>」コマンドを
 * 受けたときに、このWeb App(doPost)がHTTP POSTで呼ばれる。
 * 「通知済み」シートのjob_idを検索し、応募済み(I列)・応募日時(J列)を
 * 自動セットする（承認＝応募済み記録として統合。列は増やさない）。
 *
 * ここでも実際のクラウドワークス応募は行わない（自動応募は絶対禁止のまま）。
 *
 * 【セットアップ】
 *  1. 「設定」シートに CW_APPROVAL_SECRET（ランダムな文字列）を追加
 *  2. clasp push 後、Apps Scriptエディタで「デプロイ→新しいデプロイ」
 *     （種類=ウェブアプリ、実行者=自分、アクセス=全員）を実行しURLを取得
 *  3. 取得したURLとCW_APPROVAL_SECRETをDiscord Bot側の環境変数に設定
 */

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    const jobId = String(body.job_id || '').trim();
    const secret = String(body.secret || '');

    const expected = cwGetSetting_('CW_APPROVAL_SECRET');
    if (!expected || secret !== expected) {
      return cwJsonOutput_({ ok: false, error: 'unauthorized' });
    }
    if (!jobId) {
      return cwJsonOutput_({ ok: false, error: 'missing_job_id' });
    }

    const lock = LockService.getScriptLock();
    if (!lock.tryLock(10000)) {
      return cwJsonOutput_({ ok: false, error: 'locked_try_again' });
    }
    try {
      return cwJsonOutput_(cwApproveJob_(jobId));
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    Logger.log('doPost failed: ' + err);
    return cwJsonOutput_({ ok: false, error: 'internal_error' });
  }
}

// 疎通確認用（ブラウザで直接開いて {"status":"ok"} が返ることを確認する）
function doGet(e) {
  return cwJsonOutput_({ status: 'ok', service: 'cw-approval-webapp' });
}

function cwApproveJob_(jobId) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CW_SEEN_SHEET);
  if (!sh) return { ok: false, error: 'sheet_not_found' };

  const lastRow = sh.getLastRow();
  if (lastRow < 2) return { ok: false, error: 'not_found', job_id: jobId };

  const ids = sh.getRange(2, 1, lastRow - 1, 1).getValues().flat().map(String);
  const idx = ids.indexOf(jobId);
  if (idx === -1) return { ok: false, error: 'not_found', job_id: jobId };

  const row = idx + 2;
  const rowValues = sh.getRange(row, 1, 1, 10).getValues()[0];
  const title = rowValues[2];
  const url = rowValues[5];
  const appliedFlag = rowValues[CW_SEEN_APPLIED_COL - 1];
  const appliedAt = rowValues[CW_SEEN_APPLIED_AT_COL - 1];

  // 二重承認防止: 既にTRUE+日時ありなら書き込まず現状を返す
  if (appliedFlag === true && appliedAt) {
    return {
      ok: true, already_applied: true, job_id: jobId, title: title, url: url,
      applied_at: Utilities.formatDate(new Date(appliedAt), 'Asia/Tokyo', 'yyyy-MM-dd HH:mm'),
    };
  }

  const now = new Date();
  sh.getRange(row, CW_SEEN_APPLIED_COL).setValue(true);
  const atCell = sh.getRange(row, CW_SEEN_APPLIED_AT_COL);
  if (!atCell.getValue()) atCell.setValue(now);

  return {
    ok: true, already_applied: false, job_id: jobId, title: title, url: url,
    applied_at: Utilities.formatDate(now, 'Asia/Tokyo', 'yyyy-MM-dd HH:mm'),
  };
}

function cwJsonOutput_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
