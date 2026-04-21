// ============================================================
// IGキャンペーン候補抽出ツール - GAS版
// ============================================================

const APIFY_TOKEN = PropertiesService.getScriptProperties().getProperty('APIFY_TOKEN');
const LOG_SHEET = '利用履歴';
const SPREADSHEET_ID = '1tWNs6mSsZWFpC8e9fdvNwv9n4lVHXdsW0_xvoTqQMhg';

// ------------------------------------------------------------
// Web アプリ エントリーポイント
// ------------------------------------------------------------
function doGet() {
  return HtmlService.createHtmlOutputFromFile('index')
    .setTitle('IGキャンペーン候補抽出')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// ------------------------------------------------------------
// Apify ユーティリティ
// ------------------------------------------------------------
function startActor_(actorId, input) {
  const url = `https://api.apify.com/v2/acts/${encodeURIComponent(actorId)}/runs`;
  const res = UrlFetchApp.fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${APIFY_TOKEN}`
    },
    payload: JSON.stringify(input),
    muteHttpExceptions: true
  });
  const json = JSON.parse(res.getContentText());
  if (json.error) throw new Error(json.error.message);
  return json.data;
}

function waitForRun_(runId, maxSec) {
  const deadline = Date.now() + maxSec * 1000;
  while (Date.now() < deadline) {
    Utilities.sleep(4000);
    const res = UrlFetchApp.fetch(`https://api.apify.com/v2/actor-runs/${runId}`, {
      headers: { 'Authorization': `Bearer ${APIFY_TOKEN}` },
      muteHttpExceptions: true
    });
    const run = JSON.parse(res.getContentText()).data;
    if (run.status === 'SUCCEEDED') return run;
    if (['FAILED', 'ABORTED', 'TIMED-OUT'].includes(run.status)) {
      throw new Error(`Apify実行失敗: ${run.status}`);
    }
  }
  throw new Error('タイムアウトしました。再度お試しください。');
}

function getDataset_(datasetId) {
  const url = `https://api.apify.com/v2/datasets/${datasetId}/items?limit=1000`;
  const res = UrlFetchApp.fetch(url, {
    headers: { 'Authorization': `Bearer ${APIFY_TOKEN}` },
    muteHttpExceptions: true
  });
  return JSON.parse(res.getContentText());
}

// ------------------------------------------------------------
// スクレイピング開始（フロントから呼ぶ）
// ------------------------------------------------------------
function startLikes(postUrl) {
  const run = startActor_('datadoping/instagram-likes-scraper', {
    posts: [postUrl],
    max_count: 1000
  });
  return run.id;
}

function startComments(postUrl) {
  const run = startActor_('apify/instagram-comment-scraper', {
    directUrls: [postUrl],
    maxComments: 1000,
    maxReplies: 0
  });
  return run.id;
}

// ------------------------------------------------------------
// 結果取得・整形・ログ記録（フロントから呼ぶ）
// ------------------------------------------------------------
function getResults(params) {
  const { likesRunId, commentsRunId, mode, minFollowers, postUrl } = params;

  // いいね取得
  let likes = [];
  if (likesRunId) {
    const run = waitForRun_(likesRunId, 300);
    const items = getDataset_(run.defaultDatasetId);
    likes = items
      .map(item => ({
        username: item.username || item.userName || '',
        followerCount: item.followersCount ?? null
      }))
      .filter(i => i.username);
  }

  // コメント取得
  let comments = [];
  if (commentsRunId) {
    const run = waitForRun_(commentsRunId, 300);
    const items = getDataset_(run.defaultDatasetId);
    const map = {};
    items.forEach(item => {
      const u = item.ownerUsername || item.username || '';
      if (!u) return;
      if (!map[u]) map[u] = { username: u, followerCount: item.followersCount ?? null, comments: [] };
      if (item.text) map[u].comments.push(item.text);
    });
    comments = Object.values(map);
  }

  // 統合
  const merged = {};
  if (mode !== 'comment_only') {
    likes.forEach(l => {
      merged[l.username] = { username: l.username, followerCount: l.followerCount, comments: [] };
    });
  }
  if (mode !== 'like_only') {
    comments.forEach(c => {
      if (merged[c.username]) {
        merged[c.username].comments = c.comments;
        if (merged[c.username].followerCount === null) merged[c.username].followerCount = c.followerCount;
      } else {
        merged[c.username] = { username: c.username, followerCount: c.followerCount, comments: c.comments };
      }
    });
  }

  let results = Object.values(merged);

  // フォロワー数未取得のユーザーのみプロフィール取得
  const needProfile = results.filter(r => r.followerCount === null).map(r => r.username);
  if (needProfile.length > 0) {
    try {
      const profileRun = startActor_('apify/instagram-profile-scraper', {
        usernames: needProfile.slice(0, 1000)
      });
      const completedRun = waitForRun_(profileRun.id, 240);
      const profileItems = getDataset_(completedRun.defaultDatasetId);
      const profileMap = {};
      profileItems.forEach(item => {
        if (item.username) profileMap[item.username] = item.followersCount ?? null;
      });
      results.forEach(r => {
        if (r.followerCount === null) r.followerCount = profileMap[r.username] ?? null;
      });
    } catch(e) {
      // プロフィール取得失敗時はそのまま続行
    }
  }

  // フォロワー数フィルタ・降順ソート
  results = results.filter(r => r.followerCount !== null && r.followerCount >= minFollowers);
  results.sort((a, b) => (b.followerCount || 0) - (a.followerCount || 0));

  // 利用履歴を記録
  logUsage_(postUrl, mode, minFollowers, results.length);

  return results;
}

// ------------------------------------------------------------
// 利用履歴をスプレッドシートに記録
// ------------------------------------------------------------
function logUsage_(postUrl, mode, minFollowers, count) {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  let sheet = ss.getSheetByName(LOG_SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(LOG_SHEET);
    sheet.appendRow(['実行日時', '実行者メール', '投稿URL', 'モード', '最低フォロワー数', '抽出件数']);
    sheet.getRange(1, 1, 1, 6).setFontWeight('bold').setBackground('#4a90d9').setFontColor('#ffffff');
    sheet.setFrozenRows(1);
    sheet.setColumnWidth(1, 160);
    sheet.setColumnWidth(2, 220);
    sheet.setColumnWidth(3, 300);
  }

  let email = '（取得不可）';
  try { email = Session.getActiveUser().getEmail(); } catch(e) {}

  const modeLabels = {
    'like_only': 'いいねのみ',
    'comment_only': 'コメントのみ',
    'both_required': 'いいね＋コメント'
  };

  sheet.appendRow([
    new Date(),
    email,
    postUrl,
    modeLabels[mode] || mode,
    minFollowers,
    count
  ]);
}
