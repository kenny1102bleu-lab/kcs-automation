/**
 * Discord承認Webエンドポイント (GAS)
 *
 * Discord Bot（別系統・Render.com稼働）から呼ばれる。body の action で処理を分ける。
 *
 *  action 省略 / 'approve' : job_id の行の 応募済み(I)・応募日時(J) を埋める
 *  action 'draft'          : job_id の行の 提案下書き(H) を返す
 *  action 'skip'           : job_id の行の 見送り(K) を立てる
 *  action 'record'         : 監視に乗っていない案件を記録する（行が無ければ追加）
 *
 * ここでも実際のクラウドワークス応募は行わない（自動応募は絶対禁止のまま）。
 * やるのはスプレッドシートの読み書きだけ。送信は謙一が手で行う。
 *
 * 【セットアップ】
 *  1. 「設定」シートに CW_APPROVAL_SECRET（ランダムな文字列）を追加
 *  2. clasp push 後、Apps Scriptエディタで「デプロイ→新しいデプロイ」
 *     （種類=ウェブアプリ、実行者=自分、アクセス=全員）を実行しURLを取得
 *  3. 取得したURLとCW_APPROVAL_SECRETをDiscord Bot側の環境変数に設定
 *     ※ 貼り付けでBOM・ゼロ幅文字が混ざると「認証エラー」になる。Bot側は clean_env を
 *       通しているが、このシートの値にも見えない文字が入らないようにする
 *  4. 列の追加（見送り・名義・事業・メモ）は呼ばれたときに自動で足されるので、
 *     別途実行する関数は無い
 */

// 「通知済み」シートの列。I(9)・J(10) は applied_tracking.gs 側で定義済み
const CW_SEEN_SKIP_COL = 11;      // K列: 見送り
const CW_SEEN_ENTITY_COL = 12;    // L列: 名義（entityId）
const CW_SEEN_BUSINESS_COL = 13;  // M列: 事業（businessId）
const CW_SEEN_MEMO_COL = 14;      // N列: メモ
const CW_SEEN_LAST_COL = 14;

// 名義は入り口で決まる（追記85）。クラウドワークス経由は中身を問わず全部個人
const CW_ENTITY_ID = 'personal';
const CW_BUSINESS_ID = 'site_orders';

function doPost(e) {
  try {
    const body = JSON.parse(e.postData.contents);
    const action = String(body.action || 'approve').trim();
    const jobId = String(body.job_id || '').trim();
    const secret = String(body.secret || '');

    const expected = cwGetSetting_('CW_APPROVAL_SECRET');
    if (!expected || secret !== expected) {
      return cwJsonOutput_({ ok: false, error: 'unauthorized' });
    }

    const needsJobId = (action === 'approve' || action === 'draft' || action === 'skip');
    if (needsJobId && !jobId) {
      return cwJsonOutput_({ ok: false, error: 'missing_job_id' });
    }

    const lock = LockService.getScriptLock();
    if (!lock.tryLock(10000)) {
      return cwJsonOutput_({ ok: false, error: 'locked_try_again' });
    }
    try {
      switch (action) {
        case 'approve':
          return cwJsonOutput_(cwApproveJob_(jobId));
        case 'draft':
          return cwJsonOutput_(cwGetDraft_(jobId));
        case 'skip':
          return cwJsonOutput_(cwSkipJob_(jobId));
        case 'record':
          return cwJsonOutput_(cwRecordApplied_(String(body.ref || '').trim(), String(body.memo || '').trim()));
        default:
          return cwJsonOutput_({ ok: false, error: 'unknown_action', action: action });
      }
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

// ----------------------------------------------------
// 共通
// ----------------------------------------------------

/** 見送り・名義・事業・メモの列を、無ければ足す（何度呼んでも同じ結果）。 */
function cwEnsureSeenColumns_(sh) {
  const want = ['見送り', '名義', '事業', 'メモ'];
  const lastCol = sh.getLastColumn();
  const header = lastCol > 0 ? sh.getRange(1, 1, 1, lastCol).getValues()[0].map(String) : [];
  const missing = want.filter(function (name) { return header.indexOf(name) === -1; });
  if (missing.length === 0) return;
  // 位置をずらさないため、K〜N に固定で書く（既存のI・Jの右側）
  sh.getRange(1, CW_SEEN_SKIP_COL, 1, want.length).setValues([want]);
  const lastRow = sh.getLastRow();
  if (lastRow > 1) {
    sh.getRange(2, CW_SEEN_SKIP_COL, lastRow - 1, 1).insertCheckboxes();
  }
  Logger.log('「通知済み」シートに ' + missing.join('・') + ' 列を足しました');
}

function cwSeenSheet_() {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CW_SEEN_SHEET);
  if (!sh) return null;
  cwEnsureSeenColumns_(sh);
  return sh;
}

/** job_id の行番号を返す。無ければ -1。 */
function cwFindSeenRow_(sh, jobId) {
  const lastRow = sh.getLastRow();
  if (lastRow < 2) return -1;
  const ids = sh.getRange(2, 1, lastRow - 1, 1).getValues().flat().map(String);
  const idx = ids.indexOf(String(jobId));
  return idx === -1 ? -1 : idx + 2;
}

/** 案件URL か job_id（数字）から job_id を取り出す。取れなければ ''。 */
function cwJobIdFromRef_(ref) {
  const m = String(ref).match(/jobs\/(\d+)/);
  if (m) return m[1];
  if (/^\d+$/.test(String(ref).trim())) return String(ref).trim();
  return '';
}

function cwFmt_(d) {
  return Utilities.formatDate(new Date(d), 'Asia/Tokyo', 'yyyy-MM-dd HH:mm');
}

/** 名義・事業を埋める（既に入っていれば触らない）。 */
function cwFillOwnership_(sh, row) {
  const entity = sh.getRange(row, CW_SEEN_ENTITY_COL);
  if (!entity.getValue()) entity.setValue(CW_ENTITY_ID);
  const business = sh.getRange(row, CW_SEEN_BUSINESS_COL);
  if (!business.getValue()) business.setValue(CW_BUSINESS_ID);
}

// ----------------------------------------------------
// 処理
// ----------------------------------------------------

/** 応募済み(I)・応募日時(J) を埋める。クラウドワークスへは送らない。 */
function cwApproveJob_(jobId) {
  const sh = cwSeenSheet_();
  if (!sh) return { ok: false, error: 'sheet_not_found' };

  const row = cwFindSeenRow_(sh, jobId);
  if (row === -1) return { ok: false, error: 'not_found', job_id: jobId };

  const rowValues = sh.getRange(row, 1, 1, CW_SEEN_LAST_COL).getValues()[0];
  const title = rowValues[2];
  const url = rowValues[5];
  const appliedFlag = rowValues[CW_SEEN_APPLIED_COL - 1];
  const appliedAt = rowValues[CW_SEEN_APPLIED_AT_COL - 1];

  // 二重承認防止: 既にTRUE+日時ありなら書き込まず現状を返す
  if (appliedFlag === true && appliedAt) {
    return {
      ok: true, already_applied: true, job_id: jobId, title: title, url: url,
      applied_at: cwFmt_(appliedAt),
    };
  }

  const now = new Date();
  sh.getRange(row, CW_SEEN_APPLIED_COL).setValue(true);
  const atCell = sh.getRange(row, CW_SEEN_APPLIED_AT_COL);
  if (!atCell.getValue()) atCell.setValue(now);
  cwFillOwnership_(sh, row);

  return {
    ok: true, already_applied: false, job_id: jobId, title: title, url: url,
    entity_id: CW_ENTITY_ID, business_id: CW_BUSINESS_ID,
    applied_at: cwFmt_(now),
  };
}

/** 提案下書き(H) を返す。 */
function cwGetDraft_(jobId) {
  const sh = cwSeenSheet_();
  if (!sh) return { ok: false, error: 'sheet_not_found' };

  const row = cwFindSeenRow_(sh, jobId);
  if (row === -1) return { ok: false, error: 'not_found', job_id: jobId };

  const rowValues = sh.getRange(row, 1, 1, CW_SEEN_LAST_COL).getValues()[0];
  return {
    ok: true, job_id: jobId,
    title: rowValues[2], url: rowValues[5],
    draft: String(rowValues[7] || ''),
    applied: rowValues[CW_SEEN_APPLIED_COL - 1] === true,
  };
}

/** 見送り(K) を立てる。応募済みの案件には立てない。 */
function cwSkipJob_(jobId) {
  const sh = cwSeenSheet_();
  if (!sh) return { ok: false, error: 'sheet_not_found' };

  const row = cwFindSeenRow_(sh, jobId);
  if (row === -1) return { ok: false, error: 'not_found', job_id: jobId };

  const rowValues = sh.getRange(row, 1, 1, CW_SEEN_LAST_COL).getValues()[0];
  if (rowValues[CW_SEEN_APPLIED_COL - 1] === true) {
    return {
      ok: false, error: 'already_applied', job_id: jobId,
      title: rowValues[2], url: rowValues[5],
    };
  }
  if (rowValues[CW_SEEN_SKIP_COL - 1] === true) {
    return { ok: true, already_skipped: true, job_id: jobId, title: rowValues[2], url: rowValues[5] };
  }

  sh.getRange(row, CW_SEEN_SKIP_COL).setValue(true);
  return { ok: true, already_skipped: false, job_id: jobId, title: rowValues[2], url: rowValues[5] };
}

/**
 * 監視に乗っていない案件を記録する。行が無ければ追加する。
 * 例: 13622691（myfans動画切り抜き）は監視シートに無く、approve では not_found になる。
 */
function cwRecordApplied_(ref, memo) {
  const jobId = cwJobIdFromRef_(ref);
  if (!jobId) return { ok: false, error: 'bad_ref', ref: ref };

  const sh = cwSeenSheet_();
  if (!sh) return { ok: false, error: 'sheet_not_found' };

  const row = cwFindSeenRow_(sh, jobId);
  const now = new Date();

  if (row !== -1) {
    const rowValues = sh.getRange(row, 1, 1, CW_SEEN_LAST_COL).getValues()[0];
    const appliedFlag = rowValues[CW_SEEN_APPLIED_COL - 1];
    const appliedAt = rowValues[CW_SEEN_APPLIED_AT_COL - 1];
    if (appliedFlag === true && appliedAt) {
      return {
        ok: true, already_applied: true, created: false, job_id: jobId,
        title: rowValues[2], url: rowValues[5], applied_at: cwFmt_(appliedAt),
      };
    }
    sh.getRange(row, CW_SEEN_APPLIED_COL).setValue(true);
    const atCell = sh.getRange(row, CW_SEEN_APPLIED_AT_COL);
    if (!atCell.getValue()) atCell.setValue(now);
    cwFillOwnership_(sh, row);
    if (memo) sh.getRange(row, CW_SEEN_MEMO_COL).setValue(memo);
    return {
      ok: true, already_applied: false, created: false, job_id: jobId,
      title: rowValues[2], url: rowValues[5],
      entity_id: CW_ENTITY_ID, business_id: CW_BUSINESS_ID, applied_at: cwFmt_(now),
    };
  }

  // 監視外。新しい行を作る。タイトルは分からないので、後から手で直せるようにしておく
  const url = CW_JOB_URL_BASE + jobId;
  const title = '(手で応募・タイトル未取得)';
  sh.appendRow([
    jobId, '手入力', title, '', '', url, now, '',
    true, now, false, CW_ENTITY_ID, CW_BUSINESS_ID, memo,
  ]);
  const newRow = sh.getLastRow();
  sh.getRange(newRow, CW_SEEN_SKIP_COL).insertCheckboxes();
  sh.getRange(newRow, CW_SEEN_APPLIED_COL).insertCheckboxes();
  sh.getRange(newRow, CW_SEEN_APPLIED_COL).setValue(true);

  return {
    ok: true, already_applied: false, created: true, job_id: jobId,
    title: title, url: url,
    entity_id: CW_ENTITY_ID, business_id: CW_BUSINESS_ID, applied_at: cwFmt_(now),
  };
}

function cwJsonOutput_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
