/**
 * KCS Gmail監視（GAS）
 *
 * 1時間毎に notifications@github.com / Render / Anthropic 等からの
 * 警告・エラーメールをスキャン → Discord error-log チャンネルに通知。
 *
 * 【セットアップ】
 *  1. このスクリプトをKCS-Backend-JPに貼り付け
 *  2. setupGmailMonitorTrigger() を1回だけ実行（1時間トリガー登録）
 *
 * 【依存】「設定」シートに:
 *    DISCORD_WEBHOOK_URL_ERROR | <error-log用Webhook URL>
 *  なければ DISCORD_WEBHOOK_URL にフォールバック
 */

const GMAIL_WATCH_QUERY =
  '(from:notifications@github.com OR from:no-reply@render.com OR from:render.com OR ' +
  'from:notifications@anthropic.com OR from:noreply@google.com) ' +
  'subject:(failed OR failure OR error OR alert OR warning OR 失敗 OR エラー) ' +
  'newer_than:2h is:unread';

const PROCESSED_LABEL = 'kcs-monitored';

function setupGmailMonitorTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === 'gmailMonitorTick') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('gmailMonitorTick').timeBased().everyHours(1).create();

  // 処理済みラベル準備
  if (!GmailApp.getUserLabelByName(PROCESSED_LABEL)) {
    GmailApp.createLabel(PROCESSED_LABEL);
  }
  Logger.log('Gmail監視1時間毎トリガーを登録しました');
}

function gmailMonitorTick() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;
  try {
    const label = GmailApp.getUserLabelByName(PROCESSED_LABEL)
      || GmailApp.createLabel(PROCESSED_LABEL);

    const threads = GmailApp.search(GMAIL_WATCH_QUERY, 0, 20);
    if (threads.length === 0) return;

    const webhook = _getSetting('DISCORD_WEBHOOK_URL_ERROR')
      || _getSetting('DISCORD_WEBHOOK_URL');
    if (!webhook) {
      Logger.log('No Discord webhook configured');
      return;
    }

    threads.forEach(thread => {
      if (thread.getLabels().some(l => l.getName() === PROCESSED_LABEL)) return;
      const msg = thread.getMessages()[0];
      const subject = msg.getSubject().slice(0, 120);
      const from = msg.getFrom().slice(0, 80);
      const body = msg.getPlainBody().slice(0, 600).replace(/\s+/g, ' ');

      const payload = {
        content: '🚨 **Gmail警告検知**\n'
          + '**From:** ' + from + '\n'
          + '**Subject:** ' + subject + '\n'
          + '```\n' + body + '\n```\n'
          + (subject.match(/failed|error|エラー/i)
              ? '→ ケンジ自動解析推奨'
              : '→ 確認のみ'),
      };
      UrlFetchApp.fetch(webhook, {
        method: 'post',
        contentType: 'application/json',
        payload: JSON.stringify(payload),
        muteHttpExceptions: true,
      });

      thread.addLabel(label);
      thread.markRead();
    });

    Logger.log('Processed ' + threads.length + ' threads');
  } finally {
    lock.releaseLock();
  }
}

function _getSetting(key) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('設定');
  if (!sh) return '';
  const data = sh.getRange(1, 1, sh.getLastRow(), 2).getValues();
  for (const [k, v] of data) {
    if (String(k).trim() === key) return String(v).trim();
  }
  return '';
}
