/**
 * KCS ポジティブニュース監視 (GAS)
 *
 * 5分毎にYahoo!トピックス3カテゴリを巡回し、Gemini Flashで
 * ネガティブ記事を除外、ポジティブ候補を Pending_News シートに蓄積する。
 *
 * GitHub Actions の hal_post.py / sunakun_post.py が
 * Web App エンドポイント (doGet) から候補ネタを取得して使用する。
 *
 * 【セットアップ】
 *  1. このスクリプトをGASプロジェクトに貼り付け
 *  2. setupPendingNewsSheet() を1回だけ実行（シート作成）
 *  3. installPositiveNewsTrigger() を1回だけ実行（5分トリガー登録）
 *  4. デプロイ → 新しいデプロイ → 種類: ウェブアプリ
 *     - 実行ユーザー: 自分
 *     - アクセス権: 全員
 *     → Web App URL を GitHub Secrets に GAS_NEWS_API_URL として登録
 *
 * 【依存】
 *  「設定」シートに以下の行が必要:
 *    GEMINI_API_KEY      | <キー>
 *    DISCORD_WEBHOOK_URL | <省略可・news-pool通知用>
 */

const YAHOO_FEEDS = {
  main: 'https://news.yahoo.co.jp/rss/topics/top-picks.xml',
  entertainment: 'https://news.yahoo.co.jp/rss/topics/entertainment.xml',
  sports: 'https://news.yahoo.co.jp/rss/topics/sports.xml',
};

const SHEET_NAME = 'Pending_News';
const SETTINGS_SHEET = '設定';
const MAX_ROWS_PER_RUN = 9;       // 1巡回あたりの新規追加上限（Gemini呼び出し節約）
const RETAIN_DAYS = 7;            // status=候補 を何日保持するか

/* ─────────────────────────── セットアップ ─────────────────────────── */

function setupPendingNewsSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) sh = ss.insertSheet(SHEET_NAME);
  sh.clear();
  sh.appendRow([
    'id', 'created_at', 'category', 'title', 'url',
    'positivity', 'hal_angle', 'sunakun_angle', 'status', 'consumed_by', 'consumed_at',
  ]);
  sh.setFrozenRows(1);
  Logger.log('Pending_News シートを初期化しました');
}

function installPositiveNewsTrigger() {
  // 既存トリガー削除
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === 'positiveNewsMonitor') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('positiveNewsMonitor')
    .timeBased()
    .everyMinutes(5)
    .create();
  Logger.log('5分毎トリガーを登録しました');
}

/* ─────────────────────────── メイン処理 ─────────────────────────── */

function positiveNewsMonitor() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;
  try {
    const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
    if (!sh) { setupPendingNewsSheet(); return; }

    const lastRow = sh.getLastRow();
    const existingIds = lastRow >= 2
      ? new Set(sh.getRange(2, 1, lastRow - 1, 1).getValues().flat())
      : new Set();

    const candidates = [];
    for (const [category, url] of Object.entries(YAHOO_FEEDS)) {
      const items = fetchYahooFeed(url);
      for (const item of items) {
        const id = makeId(item.url);
        if (existingIds.has(id)) continue;
        candidates.push({ id, category, ...item });
        if (candidates.length >= MAX_ROWS_PER_RUN) break;
      }
      if (candidates.length >= MAX_ROWS_PER_RUN) break;
    }
    if (candidates.length === 0) return;

    // Gemini Flash でポジティブ判定 + 角度提案
    const judged = judgeWithGemini(candidates);
    const now = new Date();
    judged.forEach(j => {
      if (j.positivity < 6) return;  // 6点未満は捨てる
      sh.appendRow([
        j.id, now, j.category, j.title, j.url,
        j.positivity, j.hal_angle, j.sunakun_angle, '候補', '', '',
      ]);
    });

    cleanupOldRows(sh);
  } finally {
    lock.releaseLock();
  }
}

/* ─────────────────────────── Web App エンドポイント ─────────────────────────── */

/**
 * GitHub Actions から GET で呼ばれる:
 *   ?account=hal&theme_only=1   → HAL向け候補1件をJSONで返し、status=使用済にする
 *   ?account=sunakun
 *   ?account=hal&peek=1         → 取らずに先頭1件を覗く
 */
function doGet(e) {
  const account = (e.parameter.account || '').toLowerCase();
  const peek = e.parameter.peek === '1';
  if (!['hal', 'sunakun'].includes(account)) {
    return jsonResponse({ error: 'account must be hal or sunakun' }, 400);
  }
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_NAME);
  if (!sh || sh.getLastRow() < 2) return jsonResponse({ theme: null });

  const data = sh.getRange(2, 1, sh.getLastRow() - 1, 11).getValues();
  for (let i = 0; i < data.length; i++) {
    const row = data[i];
    if (row[8] !== '候補') continue;
    const angle = account === 'hal' ? row[6] : row[7];
    if (!angle) continue;

    if (!peek) {
      sh.getRange(i + 2, 9).setValue('使用済');
      sh.getRange(i + 2, 10).setValue(account);
      sh.getRange(i + 2, 11).setValue(new Date());
    }
    return jsonResponse({
      id: row[0],
      category: row[2],
      title: row[3],
      url: row[4],
      angle: angle,
    });
  }
  return jsonResponse({ theme: null });
}

function jsonResponse(obj, code) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/* ─────────────────────────── ヘルパー ─────────────────────────── */

function fetchYahooFeed(url) {
  try {
    const xml = UrlFetchApp.fetch(url, { muteHttpExceptions: true }).getContentText();
    const doc = XmlService.parse(xml);
    const root = doc.getRootElement();
    const channel = root.getChild('channel');
    if (!channel) return [];
    return channel.getChildren('item').slice(0, 10).map(it => ({
      title: it.getChildText('title') || '',
      url: it.getChildText('link') || '',
      pubDate: it.getChildText('pubDate') || '',
    }));
  } catch (err) {
    Logger.log('fetchYahooFeed failed: ' + err);
    return [];
  }
}

function judgeWithGemini(candidates) {
  const apiKey = getSetting_('GEMINI_API_KEY');
  if (!apiKey) throw new Error('GEMINI_API_KEY が「設定」シートにありません');

  const prompt = [
    'あなたはSNS投稿の素材選定アシスタントです。',
    '以下のニュース見出しリストを評価してください。',
    '事件・事故・批判・訃報・政治対立・災害は除外（positivity=0）。',
    '明るい話題・成功事例・癒し・驚き・便利は採用（positivity=6-10）。',
    '採用するものについては、',
    '  - hal_angle: HAL（21歳ハーフ女子モデル、癒し系）が共感・癒しで語る切り口（30字以内、空でも可）',
    '  - sunakun_angle: すなくん（24歳ガジェット系男子）が便利・驚き視点で語る切り口（30字以内、空でも可）',
    'を生成してください。',
    'どちらにも合わない場合はそのangleは空文字にしてください。',
    '',
    '【入力】',
    JSON.stringify(candidates.map(c => ({ id: c.id, category: c.category, title: c.title }))),
    '',
    '【出力フォーマット（JSON配列のみ、説明文なし）】',
    '[{"id":"...","positivity":0-10,"hal_angle":"...","sunakun_angle":"..."}]',
  ].join('\n');

  const endpoint = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key=' + apiKey;
  const resp = UrlFetchApp.fetch(endpoint, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({
      contents: [{ parts: [{ text: prompt }] }],
      generationConfig: { temperature: 0.4, responseMimeType: 'application/json' },
    }),
    muteHttpExceptions: true,
  });
  const text = JSON.parse(resp.getContentText()).candidates?.[0]?.content?.parts?.[0]?.text || '[]';
  let parsed;
  try { parsed = JSON.parse(text); } catch { parsed = []; }

  const map = Object.fromEntries(parsed.map(p => [p.id, p]));
  return candidates.map(c => ({
    ...c,
    positivity: map[c.id]?.positivity ?? 0,
    hal_angle: map[c.id]?.hal_angle || '',
    sunakun_angle: map[c.id]?.sunakun_angle || '',
  }));
}

function getSetting_(key) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SETTINGS_SHEET);
  if (!sh) return '';
  const data = sh.getRange(1, 1, sh.getLastRow(), 2).getValues();
  for (const [k, v] of data) {
    if (String(k).trim() === key) return String(v).trim();
  }
  return '';
}

function makeId(url) {
  return Utilities.computeDigest(Utilities.DigestAlgorithm.MD5, url)
    .map(b => (b & 0xff).toString(16).padStart(2, '0')).join('').slice(0, 12);
}

function cleanupOldRows(sh) {
  const cutoff = new Date(Date.now() - RETAIN_DAYS * 24 * 3600 * 1000);
  const last = sh.getLastRow();
  if (last < 2) return;
  const dates = sh.getRange(2, 2, last - 1, 1).getValues();
  for (let i = dates.length - 1; i >= 0; i--) {
    if (dates[i][0] instanceof Date && dates[i][0] < cutoff) {
      sh.deleteRow(i + 2);
    }
  }
}
