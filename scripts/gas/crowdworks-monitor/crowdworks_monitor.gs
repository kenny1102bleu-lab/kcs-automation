/**
 * クラウドワークス新着案件監視 (GAS)
 *
 * 「設定」シートに登録したキーワードで案件検索結果(order=new)を定期チェックし、
 * 未通知の新着案件をDiscordへ通知する。応募・提案の自動化は行わない
 * （応募文はクライアントごとにカスタマイズが必要かつ規約上の理由）。
 *
 * 【セットアップ】
 *  1. setupCrowdworksMonitor() を1回実行 → 「設定」「通知済み」シートを作成
 *  2. 「設定」シートに CW_KEYWORDS（カンマ区切り、例: Claude Code,業務自動化,GAS）
 *     と DISCORD_WEBHOOK_URL を入力
 *  3. installCrowdworksMonitorTrigger() を1回実行 → 30分毎トリガー登録
 *
 * 【注意】
 *  クラウドワークスの新着案件RSSは廃止済みのため、検索結果ページのサーバー
 *  サイドレンダリングデータ（#vue-container の data 属性）を読み取っている。
 *  公式提供のAPIではないため、規約上グレーな部分がある。過剰アクセスと
 *  見なされないよう、トリガー間隔は30分以上を維持し、キーワード数も
 *  絞ること（自動応募・自動投稿は絶対に行わない）。
 */

const CW_SETTINGS_SHEET = '設定';
const CW_SEEN_SHEET = '通知済み';
const CW_SEARCH_BASE = 'https://crowdworks.jp/public/jobs/search';
const CW_JOB_URL_BASE = 'https://crowdworks.jp/public/jobs/';
const CW_MAX_NOTIFY_PER_TICK = 10; // 1回のtickで通知する上限（暴走防止）
const CW_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
  + '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';

function setupCrowdworksMonitor() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  let settings = ss.getSheetByName(CW_SETTINGS_SHEET);
  if (!settings) settings = ss.insertSheet(CW_SETTINGS_SHEET);
  if (settings.getLastRow() === 0) {
    settings.appendRow(['CW_KEYWORDS', '']); // 例: Claude Code,業務自動化,GAS
    settings.appendRow(['DISCORD_WEBHOOK_URL', '']);
    settings.setFrozenRows(1);
  }

  let seen = ss.getSheetByName(CW_SEEN_SHEET);
  if (!seen) seen = ss.insertSheet(CW_SEEN_SHEET);
  if (seen.getLastRow() === 0) {
    seen.appendRow(['job_id', 'keyword', 'title', 'budget', 'client', 'url', 'notified_at', '提案下書き', '応募済み', '応募日時']);
    seen.setFrozenRows(1);
    seen.getRange(2, 9, 998, 1).insertCheckboxes();
  }

  Logger.log('セットアップ完了。「設定」シートにCW_KEYWORDSとDISCORD_WEBHOOK_URLを入力してください。');
}

function installCrowdworksMonitorTrigger() {
  ScriptApp.getProjectTriggers().forEach(t => {
    if (t.getHandlerFunction() === 'crowdworksMonitorTick') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('crowdworksMonitorTick').timeBased().everyMinutes(30).create();
  Logger.log('30分毎トリガーを登録しました');
}

function crowdworksMonitorTick() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const seenSheet = ss.getSheetByName(CW_SEEN_SHEET);
    if (!seenSheet) { setupCrowdworksMonitor(); return; }

    const keywords = cwGetSetting_('CW_KEYWORDS').split(',').map(s => s.trim()).filter(Boolean);
    if (keywords.length === 0) {
      Logger.log('CW_KEYWORDSが未設定です');
      return;
    }
    const webhook = cwGetSetting_('DISCORD_WEBHOOK_URL');

    const lastRow = seenSheet.getLastRow();
    const isFirstRun = lastRow < 2;
    const seenIds = isFirstRun
      ? new Set()
      : new Set(seenSheet.getRange(2, 1, lastRow - 1, 1).getValues().flat().map(String));

    const newlyFound = [];
    for (const keyword of keywords) {
      const jobs = cwFetchJobs_(keyword);
      for (const job of jobs) {
        const id = String(job.id);
        if (seenIds.has(id)) continue;
        seenIds.add(id); // 同一tick内で複数キーワードにヒットした場合の重複防止
        newlyFound.push(Object.assign({ keyword: keyword }, job));
      }
    }
    if (newlyFound.length === 0) return;

    const now = new Date();
    newlyFound.forEach((job, i) => {
      let draft = '';
      if (!isFirstRun && i < CW_MAX_NOTIFY_PER_TICK) {
        draft = cwDraftProposal_(job);
      }
      seenSheet.appendRow([job.id, job.keyword, job.title, job.budgetText, job.client, job.url, now, draft]);
      if (!isFirstRun && webhook && i < CW_MAX_NOTIFY_PER_TICK) {
        cwNotifyDiscord_(webhook, Object.assign({ draft: draft }, job));
      }
    });

    if (isFirstRun) {
      Logger.log('初回実行: ' + newlyFound.length + '件を既知案件として登録（通知はスキップ）');
    } else {
      Logger.log(Math.min(newlyFound.length, CW_MAX_NOTIFY_PER_TICK) + '件を通知（新着' + newlyFound.length + '件検出）');
    }
  } finally {
    lock.releaseLock();
  }
}

function cwFetchJobs_(keyword) {
  const url = CW_SEARCH_BASE + '?keywords=' + encodeURIComponent(keyword) + '&order=new';
  const res = UrlFetchApp.fetch(url, {
    method: 'get',
    headers: { 'User-Agent': CW_UA, 'Accept-Language': 'ja,en-US;q=0.9,en;q=0.8' },
    muteHttpExceptions: true,
  });
  if (res.getResponseCode() !== 200) {
    Logger.log('fetch failed (' + keyword + '): status=' + res.getResponseCode());
    return [];
  }
  const body = res.getContentText();
  const m = body.match(/id="vue-container"\s+data="([^"]*)"/);
  if (!m) {
    Logger.log('vue-container not found (' + keyword + ')');
    return [];
  }

  let json;
  try {
    json = JSON.parse(cwUnescapeHtml_(m[1]));
  } catch (e) {
    Logger.log('JSON parse失敗 (' + keyword + '): ' + e);
    return [];
  }

  const offers = (json.searchResult && json.searchResult.job_offers) || [];
  return offers.map(o => ({
    id: o.job_offer.id,
    title: o.job_offer.title,
    description: o.job_offer.description_digest || '',
    url: CW_JOB_URL_BASE + o.job_offer.id,
    budgetText: cwFormatBudget_(o.payment),
    client: (o.client && o.client.username) || '',
  }));
}

function cwFormatBudget_(payment) {
  if (!payment) return '不明';
  if (payment.fixed_price_payment) {
    const p = payment.fixed_price_payment;
    return '固定: ' + cwYen_(p.min_budget) + '〜' + cwYen_(p.max_budget);
  }
  if (payment.hourly_payment) {
    const p = payment.hourly_payment;
    return '時給: ' + cwYen_(p.min_hourly) + '〜' + cwYen_(p.max_hourly);
  }
  if (payment.task_payment) {
    return 'タスク: ' + cwYen_(payment.task_payment.price);
  }
  return '不明';
}

function cwYen_(n) {
  return n ? Math.round(n).toLocaleString('ja-JP') + '円' : '-';
}

function cwUnescapeHtml_(s) {
  return s
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&');
}

function cwNotifyDiscord_(webhook, job) {
  let content = '🆕 **新着案件**（キーワード: ' + job.keyword + '）\n'
    + '**' + job.title + '**\n'
    + 'ID: ' + job.id + '\n'
    + '報酬: ' + job.budgetText + ' / クライアント: ' + job.client + '\n'
    + job.url;
  if (job.draft) {
    content += '\n\n✍️ **提案文たたき台**\n' + job.draft;
  }
  content += '\n\n承認: `!CW承認 ' + job.id + '`';
  const res = UrlFetchApp.fetch(webhook, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({ content: content }),
    muteHttpExceptions: true,
  });
  const code = res.getResponseCode();
  if (code < 200 || code >= 300) {
    Logger.log('Discord送信失敗 status=' + code + ' body=' + res.getContentText());
  }
}

function cwDraftProposal_(job) {
  const apiKey = cwGetSetting_('GEMINI_API_KEY');
  if (!apiKey) return '';

  const prompt = [
    'あなたはクラウドワークスで高単価のAI活用・業務自動化案件を受注しているフリーランスエンジニアです。',
    '実績: Google Apps Script(GAS)とn8nによる業務自動化、Claude Code/Claude APIを使ったシステム開発。',
    '以下の案件に応募する提案文の「たたき台」を150〜220字程度の日本語で書いてください。',
    '',
    '厳守ルール:',
    '- 「させていただきます」の連発、絵文字の多用、テンプレ感のある定型挨拶は避ける',
    '- 案件の内容に具体的に触れ、なぜ自分が適任かを一言添える',
    '- 見積もりや納期など、案件を読まないと分からない情報は書かない（後で人間が調整する前提のたたき台）',
    '- 説明文なし、提案文の本文のみを出力する',
    '',
    '【案件タイトル】' + job.title,
    '【案件詳細】' + (job.description || '(詳細なし)').slice(0, 500),
  ].join('\n');

  try {
    const res = UrlFetchApp.fetch(
      'https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key=' + apiKey,
      {
        method: 'post',
        contentType: 'application/json',
        payload: JSON.stringify({
          contents: [{ parts: [{ text: prompt }] }],
          generationConfig: { temperature: 0.6 },
        }),
        muteHttpExceptions: true,
      }
    );
    const raw = res.getContentText();
    const json = JSON.parse(raw);
    const text = json.candidates && json.candidates[0] && json.candidates[0].content
      && json.candidates[0].content.parts && json.candidates[0].content.parts[0].text;
    if (!text) {
      Logger.log('提案文生成: テキスト抽出失敗 status=' + res.getResponseCode() + ' body=' + raw.slice(0, 800));
    }
    return (text || '').trim();
  } catch (e) {
    Logger.log('提案文生成失敗: ' + e);
    return '';
  }
}

function cwGetSetting_(key) {
  const sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(CW_SETTINGS_SHEET);
  if (!sh) return '';
  const data = sh.getRange(1, 1, sh.getLastRow(), 2).getValues();
  for (const [k, v] of data) {
    if (String(k).trim() === key) return String(v).trim();
  }
  return '';
}
