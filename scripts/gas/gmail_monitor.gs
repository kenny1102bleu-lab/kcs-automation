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
  '(from:no-reply@marketing.base44.com OR from:chelsea.c@ifttt.com OR ' +
  'from:em@em1.cloudflare.com OR from:notifications@github.com OR ' +
  'from:noreply@us2.make.com) ' +
  '(subject:(error OR failed OR limit OR quota OR inactive OR alert OR warning) ' +
  'OR "error" OR "failed" OR "limit") ' +
  'newer_than:2h is:unread';

const PROCESSED_LABEL = 'kcs-monitored';
const HEARTBEAT_KEY = 'GMAIL_LAST_RUN_TS';
const HEARTBEAT_THRESHOLD_MS = 60 * 60 * 1000;  // 60分

function setupGmailMonitorTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    const fn = t.getHandlerFunction();
    if (fn === 'gmailMonitorTick' || fn === 'gmailMonitorHeartbeatCheck') {
      ScriptApp.deleteTrigger(t);
    }
  });
  ScriptApp.newTrigger('gmailMonitorTick').timeBased().everyHours(1).create();
  // ハートビートチェックは30分毎（脱落検知）
  ScriptApp.newTrigger('gmailMonitorHeartbeatCheck').timeBased().everyMinutes(30).create();

  if (!GmailApp.getUserLabelByName(PROCESSED_LABEL)) {
    GmailApp.createLabel(PROCESSED_LABEL);
  }
  // 初期ハートビート登録
  PropertiesService.getScriptProperties().setProperty(HEARTBEAT_KEY, String(Date.now()));
  Logger.log('Gmail監視: 1時間毎トリガー＋30分毎ハートビートを登録しました');
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
    PropertiesService.getScriptProperties().setProperty(HEARTBEAT_KEY, String(Date.now()));
  } finally {
    lock.releaseLock();
  }
}

/**
 * ハートビートチェック: 60分以上 gmailMonitorTick が走っていなければ
 * トリガー脱落と判定し再セットアップする。別トリガーで5分毎などに走らせる前提。
 */
function gmailMonitorHeartbeatCheck() {
  const lastStr = PropertiesService.getScriptProperties().getProperty(HEARTBEAT_KEY);
  const last = lastStr ? Number(lastStr) : 0;
  const drift = Date.now() - last;
  if (last && drift < HEARTBEAT_THRESHOLD_MS) return;

  Logger.log('Heartbeat stale (drift=' + Math.round(drift / 60000) + 'min) → re-installing triggers');
  setupGmailMonitorTrigger();

  const webhook = _getSetting('DISCORD_WEBHOOK_URL_ERROR') || _getSetting('DISCORD_WEBHOOK_URL');
  if (webhook) {
    UrlFetchApp.fetch(webhook, {
      method: 'post',
      contentType: 'application/json',
      payload: JSON.stringify({
        content: '🩺 **自己修復**: Gmail監視トリガー脱落を検知→再セットアップ完了 (drift=' +
          Math.round(drift / 60000) + '分)',
      }),
      muteHttpExceptions: true,
    });
  }
  // 即時1回実行
  gmailMonitorTick();
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
