/**
 * 受注後フロー管理 (GAS)
 *
 * 「案件管理」シートのステータス列を人間が更新すると、
 * 進捗をDiscordに通知し、制作中になったらDriveフォルダを自動作成、
 * 納品済みになったらGmail下書き（送信はしない）を自動作成する。
 *
 * 受注（クライアント確定）は手動でこのシートに行を追加する運用。
 * 金銭・対外送信に関わる最終アクション（メール送信）は必ず人間が確認して送る。
 *
 * 【セットアップ】
 *  1. setupOrderManagement() を1回実行 → 「案件管理」シート作成＋Drive親フォルダ作成
 *  2. installOrderManagementTrigger() を1回実行
 *     → 編集トリガー（ステータス変更検知）＋毎日9時の滞留チェックトリガーを登録
 *  3. 受注が決まったら「案件管理」シートに1行追加し、ステータスを更新していく
 */

const CW_ORDER_SHEET = '案件管理';
const CW_ORDER_STATUS_COL = 8;   // H列: ステータス
const CW_ORDER_FOLDER_COL = 10;  // J列: Driveフォルダ
const CW_ORDER_UPDATED_COL = 11; // K列: 更新日時
const CW_ORDER_STATUSES = ['ヒアリング中', '制作中', 'レビュー中', '納品済み'];
const CW_STALE_DAYS = 3;

function setupOrderManagement() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(CW_ORDER_SHEET);
  if (!sh) sh = ss.insertSheet(CW_ORDER_SHEET);
  if (sh.getLastRow() === 0) {
    sh.appendRow([
      'id', '案件名', 'クライアント名', 'クライアントメール', '受注日',
      '金額', '納期', 'ステータス', 'ヒアリング内容', 'Driveフォルダ', '更新日時',
    ]);
    sh.setFrozenRows(1);
    const rule = SpreadsheetApp.newDataValidation().requireValueInList(CW_ORDER_STATUSES, true).build();
    sh.getRange(2, CW_ORDER_STATUS_COL, 500, 1).setDataValidation(rule);
  }

  if (!cwGetSetting_('CW_PROJECT_ROOT_FOLDER_ID')) {
    const folder = DriveApp.createFolder('クラウドワークス案件_納品物');
    cwSetSetting_('CW_PROJECT_ROOT_FOLDER_ID', folder.getId());
    Logger.log('案件フォルダの親フォルダを作成しました: ' + folder.getUrl());
  }

  Logger.log('案件管理シートをセットアップしました。受注したら1行ずつ追加してください。');
}

function installOrderManagementTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    const fn = t.getHandlerFunction();
    if (fn === 'cwOrderOnEdit' || fn === 'cwOrderStaleCheck') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('cwOrderOnEdit')
    .forSpreadsheet(SpreadsheetApp.getActiveSpreadsheet())
    .onEdit()
    .create();
  ScriptApp.newTrigger('cwOrderStaleCheck')
    .timeBased().everyDays(1).atHour(9)
    .create();
  Logger.log('編集トリガーと毎日9時の滞留チェックトリガーを登録しました');
}

function cwOrderOnEdit(e) {
  const sh = e.range.getSheet();
  if (sh.getName() !== CW_ORDER_SHEET) return;
  if (e.range.getColumn() !== CW_ORDER_STATUS_COL || e.range.getRow() === 1) return;

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;
  try {
    const row = e.range.getRow();
    const rowValues = sh.getRange(row, 1, 1, 11).getValues()[0];
    const [id, projectName, clientName, , , budget, , status] = rowValues;

    sh.getRange(row, CW_ORDER_UPDATED_COL).setValue(new Date());

    const webhook = cwGetSetting_('DISCORD_WEBHOOK_URL');
    if (webhook) {
      cwOrderDiscordSend_(webhook, '📋 **進行更新**: 「' + projectName + '」（' + clientName + '）→ ' + status);
    }

    if (status === '制作中' && !rowValues[CW_ORDER_FOLDER_COL - 1]) {
      const rootId = cwGetSetting_('CW_PROJECT_ROOT_FOLDER_ID');
      const root = rootId ? DriveApp.getFolderById(rootId) : DriveApp.createFolder('クラウドワークス案件_納品物');
      const folder = root.createFolder((id || row) + '_' + projectName);
      sh.getRange(row, CW_ORDER_FOLDER_COL).setValue(folder.getUrl());
    }

    if (status === '納品済み') {
      cwCreateDeliveryDraft_(sh, row);
    }
  } finally {
    lock.releaseLock();
  }
}

function cwOrderStaleCheck() {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CW_ORDER_SHEET);
  if (!sh || sh.getLastRow() < 2) return;
  const webhook = cwGetSetting_('DISCORD_WEBHOOK_URL');
  if (!webhook) return;

  const data = sh.getRange(2, 1, sh.getLastRow() - 1, 11).getValues();
  const now = Date.now();
  data.forEach(rowValues => {
    const projectName = rowValues[1];
    const status = rowValues[CW_ORDER_STATUS_COL - 1];
    const updatedAt = rowValues[CW_ORDER_UPDATED_COL - 1];
    if (!status || status === '納品済み') return;
    if (!(updatedAt instanceof Date)) return;

    const days = (now - updatedAt.getTime()) / 86400000;
    if (days >= CW_STALE_DAYS) {
      cwOrderDiscordSend_(webhook, '⏰ **滞留アラート**: 「' + projectName + '」が「' + status + '」のまま' + Math.floor(days) + '日経過しています。');
    }
  });
}

function cwCreateDeliveryDraft_(sh, row) {
  const rowValues = sh.getRange(row, 1, 1, 11).getValues()[0];
  const [, projectName, clientName, clientEmail, , budget] = rowValues;
  const driveFolderUrl = rowValues[CW_ORDER_FOLDER_COL - 1];
  const webhook = cwGetSetting_('DISCORD_WEBHOOK_URL');

  if (!clientEmail) {
    if (webhook) {
      cwOrderDiscordSend_(webhook, '⚠️ 「' + projectName + '」納品済みですが、クライアントメールが未入力のため下書きを作成できませんでした。D列に入力後、ステータスを再度保存してください。');
    }
    return;
  }

  const subject = '【納品のご連絡】' + projectName;
  const body = clientName + ' 様\n\n'
    + 'お世話になっております。\n'
    + '「' + projectName + '」の納品が完了いたしましたのでご連絡いたします。\n\n'
    + '■ 成果物\n' + (driveFolderUrl || '(フォルダURL未設定)') + '\n\n'
    + '■ 金額\n' + (budget || '(未設定)') + '\n\n'
    + 'ご確認のほど、よろしくお願いいたします。\n';
  GmailApp.createDraft(clientEmail, subject, body);

  if (webhook) {
    cwOrderDiscordSend_(webhook, '📦 **納品メール下書き作成**: 「' + projectName + '」宛の下書きをGmailに作成しました。内容を確認のうえ、ご自身で送信してください（自動送信はしていません）。');
  }
}

function cwOrderDiscordSend_(webhook, content) {
  const res = UrlFetchApp.fetch(webhook, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({ content: content }),
    muteHttpExceptions: true,
  });
  const code = res.getResponseCode();
  if (code < 200 || code >= 300) {
    Logger.log('Discord送信失敗 status=' + code + ' body=' + res.getContentText());
  } else {
    Logger.log('Discord送信成功 status=' + code);
  }
}

function cwSetSetting_(key, value) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CW_SETTINGS_SHEET);
  const data = sh.getRange(1, 1, sh.getLastRow(), 2).getValues();
  for (let i = 0; i < data.length; i++) {
    if (String(data[i][0]).trim() === key) {
      sh.getRange(i + 1, 2).setValue(value);
      return;
    }
  }
  sh.appendRow([key, value]);
}
