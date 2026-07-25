/**
 * 応募済みチェック (GAS)
 *
 * 「通知済み」シートの「応募済み」列にチェックを入れると、
 * 「応募日時」列に自動で日時が記録される。
 * （応募操作そのものはクラウドワークス上で人間が行う。ここで自動化するのは
 *  「応募した/していない」を後から管理しやすくする記録作業のみ）
 *
 * 既存の「通知済み」シートに列を追加する場合は cwUpgradeSeenSheetAddApplied() を
 * 1回実行してから installAppliedTrackingTrigger() を実行する。
 */

const CW_SEEN_APPLIED_COL = 9;    // I列: 応募済み
const CW_SEEN_APPLIED_AT_COL = 10; // J列: 応募日時

function cwUpgradeSeenSheetAddApplied() {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CW_SEEN_SHEET);
  const header = sh.getRange(1, 1, 1, sh.getLastColumn()).getValues()[0];
  if (header.indexOf('応募済み') === -1) {
    sh.getRange(1, sh.getLastColumn() + 1, 1, 2).setValues([['応募済み', '応募日時']]);
  }
  const lastRow = sh.getLastRow();
  if (lastRow > 1) {
    sh.getRange(2, CW_SEEN_APPLIED_COL, lastRow - 1, 1).insertCheckboxes();
  }
  Logger.log('「通知済み」シートに応募済み・応募日時列を追加しました');
}

function installAppliedTrackingTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === 'cwSeenOnEdit') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('cwSeenOnEdit')
    .forSpreadsheet(SpreadsheetApp.getActiveSpreadsheet())
    .onEdit()
    .create();
  Logger.log('応募済みチェックのトリガーを登録しました');
}

function cwSeenOnEdit(e) {
  const sh = e.range.getSheet();
  if (sh.getName() !== CW_SEEN_SHEET) return;
  if (e.range.getColumn() !== CW_SEEN_APPLIED_COL || e.range.getRow() === 1) return;
  if (e.value !== 'TRUE') return;

  const atCell = sh.getRange(e.range.getRow(), CW_SEEN_APPLIED_AT_COL);
  if (!atCell.getValue()) atCell.setValue(new Date());
}
