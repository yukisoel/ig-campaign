// ============================================================
// IGキャンペーン候補抽出ツール - GAS版（マルチアカウント・ジョブ管理対応）
// ============================================================

const PROPS = PropertiesService.getScriptProperties();
const APIFY_TOKEN = PROPS.getProperty('APIFY_TOKEN');
const SPREADSHEET_ID = PROPS.getProperty('SPREADSHEET_ID') || SpreadsheetApp.getActiveSpreadsheet().getId();
const SHEET_JOBS = 'ジョブ管理';
const SHEET_ACCOUNTS = 'アカウント管理';
const SHEET_LOG = '利用履歴';

// ------------------------------------------------------------
// Web アプリ エントリーポイント
// ------------------------------------------------------------
function doGet() {
  return HtmlService.createHtmlOutputFromFile('index')
    .setTitle('IGキャンペーン候補抽出')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// ============================================================
// アカウント管理
// ============================================================

function getAccountsSheet_() {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  let sheet = ss.getSheetByName(SHEET_ACCOUNTS);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_ACCOUNTS);
    sheet.appendRow(['表示名', '備考']);
    sheet.getRange(1, 1, 1, 2).setFontWeight('bold').setBackground('#4a90d9').setFontColor('#fff');
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function getAccounts() {
  const sheet = getAccountsSheet_();
  const lastRow = sheet.getLastRow();
  if (lastRow <= 1) return [];
  const data = sheet.getRange(2, 1, lastRow - 1, 2).getValues();
  return data.map(row => ({
    name: row[0],
    note: row[1] || ''
  }));
}

function addAccount(name, note) {
  if (!name) throw new Error('表示名を入力してください');
  const accounts = getAccounts();
  if (accounts.some(a => a.name === name)) {
    throw new Error('同じ表示名が既に存在します');
  }
  const sheet = getAccountsSheet_();
  sheet.appendRow([name, note || '']);
}

function deleteAccount(name) {
  const sheet = getAccountsSheet_();
  const lastRow = sheet.getLastRow();
  for (let i = 2; i <= lastRow; i++) {
    if (sheet.getRange(i, 1).getValue() === name) {
      sheet.deleteRow(i);
      return;
    }
  }
}

// ============================================================
// ジョブ管理
// ============================================================

function getJobsSheet_() {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  let sheet = ss.getSheetByName(SHEET_JOBS);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_JOBS);
    // columns: ID, ステータス, 作成日時, アカウント名, 投稿URL, モード, 最低フォロワー, ログ, エラー, 結果JSON, いいねRunID, コメントRunID
    sheet.appendRow(['ID', 'ステータス', '作成日時', 'アカウント名', '投稿URL', 'モード', '最低フォロワー', 'ログ', 'エラー', '結果JSON', 'いいねRunID', 'コメントRunID']);
    sheet.getRange(1, 1, 1, 12).setFontWeight('bold').setBackground('#4a90d9').setFontColor('#fff');
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function findJobRow_(sheet, jobId) {
  const lastRow = sheet.getLastRow();
  if (lastRow <= 1) return -1;
  const ids = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
  for (let i = 0; i < ids.length; i++) {
    if (ids[i][0] === jobId) return i + 2;
  }
  return -1;
}

function getJobs() {
  const sheet = getJobsSheet_();
  const lastRow = sheet.getLastRow();
  if (lastRow <= 1) return [];
  const data = sheet.getRange(2, 1, lastRow - 1, 12).getValues();
  return data.map(row => ({
    id: row[0],
    status: row[1],
    created_at: row[2],
    account_name: row[3],
    post_url: row[4],
    mode: row[5],
    min_followers: row[6],
    log: row[7] ? row[7].split('\n') : [],
    error: row[8] || null,
    has_result: !!row[9],
    likes_run_id: row[10] || null,
    comments_run_id: row[11] || null
  }));
}

function createJob(params) {
  const jobId = Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyyMMdd_HHmmss') + '_' + Utilities.getUuid().substring(0, 6);
  const sheet = getJobsSheet_();
  sheet.appendRow([
    jobId,
    'waiting',
    Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyy-MM-dd HH:mm:ss'),
    params.account_name,
    params.post_url,
    params.mode,
    params.min_followers,
    '', // log
    '', // error
    '', // result JSON
    '', // likes run id
    ''  // comments run id
  ]);
  return jobId;
}

function updateJobField_(jobId, colIndex, value) {
  const sheet = getJobsSheet_();
  const row = findJobRow_(sheet, jobId);
  if (row === -1) return;
  sheet.getRange(row, colIndex).setValue(value);
}

function appendJobLog_(jobId, message) {
  const sheet = getJobsSheet_();
  const row = findJobRow_(sheet, jobId);
  if (row === -1) return;
  const cell = sheet.getRange(row, 8); // log column
  const current = cell.getValue();
  cell.setValue(current ? current + '\n' + message : message);
}

function deleteJob(jobId) {
  const sheet = getJobsSheet_();
  const row = findJobRow_(sheet, jobId);
  if (row === -1) return;
  sheet.deleteRow(row);
}

function getJobResult(jobId) {
  const sheet = getJobsSheet_();
  const row = findJobRow_(sheet, jobId);
  if (row === -1) return null;
  const json = sheet.getRange(row, 10).getValue();
  if (!json) return null;
  return JSON.parse(json);
}

// ============================================================
// Apify ユーティリティ
// ============================================================

function startActor_(actorId, input) {
  const url = 'https://api.apify.com/v2/acts/' + encodeURIComponent(actorId) + '/runs';
  const res = UrlFetchApp.fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + APIFY_TOKEN
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
    Utilities.sleep(5000);
    const res = UrlFetchApp.fetch('https://api.apify.com/v2/actor-runs/' + runId, {
      headers: { 'Authorization': 'Bearer ' + APIFY_TOKEN },
      muteHttpExceptions: true
    });
    const run = JSON.parse(res.getContentText()).data;
    if (run.status === 'SUCCEEDED') return run;
    if (['FAILED', 'ABORTED', 'TIMED-OUT'].indexOf(run.status) !== -1) {
      throw new Error('Apify実行失敗: ' + run.status);
    }
  }
  throw new Error('タイムアウトしました。再度お試しください。');
}

function getDataset_(datasetId) {
  const url = 'https://api.apify.com/v2/datasets/' + datasetId + '/items?limit=1000';
  const res = UrlFetchApp.fetch(url, {
    headers: { 'Authorization': 'Bearer ' + APIFY_TOKEN },
    muteHttpExceptions: true
  });
  return JSON.parse(res.getContentText());
}

// ============================================================
// スクレイピング開始（フロントから呼ぶ）
// ============================================================

function startExtraction(jobId) {
  const sheet = getJobsSheet_();
  const row = findJobRow_(sheet, jobId);
  if (row === -1) throw new Error('ジョブが見つかりません');

  const rowData = sheet.getRange(row, 1, 1, 12).getValues()[0];
  const postUrl = rowData[4];
  const mode = rowData[5];
  const minFollowers = rowData[6];

  // ステータス更新
  updateJobField_(jobId, 2, 'running');
  appendJobLog_(jobId, 'スクレイピングを開始...');

  try {
    var likesRunId = null;
    var commentsRunId = null;

    // いいね取得開始
    if (mode === 'like_only' || mode === 'both_required') {
      appendJobLog_(jobId, 'いいね取得を開始...');
      var likeRun = startActor_('datadoping/instagram-likes-scraper', {
        posts: [postUrl],
        max_count: 1000
      });
      likesRunId = likeRun.id;
      updateJobField_(jobId, 11, likesRunId);
      appendJobLog_(jobId, 'いいね取得中 (RunID: ' + likesRunId + ')');
    }

    // コメント取得開始
    if (mode === 'comment_only' || mode === 'both_required') {
      appendJobLog_(jobId, 'コメント取得を開始...');
      var commentRun = startActor_('apify/instagram-comment-scraper', {
        directUrls: [postUrl],
        maxComments: 1000,
        maxReplies: 0
      });
      commentsRunId = commentRun.id;
      updateJobField_(jobId, 12, commentsRunId);
      appendJobLog_(jobId, 'コメント取得中 (RunID: ' + commentsRunId + ')');
    }

    // いいね結果待機
    var likes = [];
    if (likesRunId) {
      var likeResult = waitForRun_(likesRunId, 300);
      var likeItems = getDataset_(likeResult.defaultDatasetId);
      likes = likeItems
        .map(function(item) {
          return {
            username: item.username || item.userName || '',
            followerCount: item.followersCount != null ? item.followersCount : null
          };
        })
        .filter(function(i) { return i.username; });
      appendJobLog_(jobId, 'いいね取得完了: ' + likes.length + ' 件');
    }

    // コメント結果待機
    var comments = [];
    if (commentsRunId) {
      var commentResult = waitForRun_(commentsRunId, 300);
      var commentItems = getDataset_(commentResult.defaultDatasetId);
      var commentMap = {};
      commentItems.forEach(function(item) {
        var u = item.ownerUsername || item.username || '';
        if (!u) return;
        if (!commentMap[u]) commentMap[u] = { username: u, followerCount: item.followersCount != null ? item.followersCount : null, comments: [] };
        if (item.text) commentMap[u].comments.push(item.text);
      });
      comments = Object.values(commentMap);
      appendJobLog_(jobId, 'コメント取得完了: ' + comments.length + ' ユーザー');
    }

    // 統合
    var merged = {};
    if (mode !== 'comment_only') {
      likes.forEach(function(l) {
        merged[l.username] = { username: l.username, followerCount: l.followerCount, comments: [] };
      });
    }
    if (mode !== 'like_only') {
      comments.forEach(function(c) {
        if (merged[c.username]) {
          merged[c.username].comments = c.comments;
          if (merged[c.username].followerCount == null) merged[c.username].followerCount = c.followerCount;
        } else {
          merged[c.username] = { username: c.username, followerCount: c.followerCount, comments: c.comments };
        }
      });
    }

    var results = Object.values(merged);

    // フォロワー数未取得のユーザーのみプロフィール取得
    var needProfile = results.filter(function(r) { return r.followerCount == null; }).map(function(r) { return r.username; });
    if (needProfile.length > 0) {
      appendJobLog_(jobId, 'フォロワー数を取得中 (' + needProfile.length + ' 人)...');
      try {
        var profileRun = startActor_('apify/instagram-profile-scraper', {
          usernames: needProfile.slice(0, 1000)
        });
        var completedProfile = waitForRun_(profileRun.id, 240);
        var profileItems = getDataset_(completedProfile.defaultDatasetId);
        var profileMap = {};
        profileItems.forEach(function(item) {
          if (item.username) profileMap[item.username] = item.followersCount != null ? item.followersCount : null;
        });
        results.forEach(function(r) {
          if (r.followerCount == null) r.followerCount = profileMap[r.username] != null ? profileMap[r.username] : null;
        });
        appendJobLog_(jobId, 'フォロワー数取得完了');
      } catch(e) {
        appendJobLog_(jobId, 'フォロワー数取得失敗（続行）: ' + e.message);
      }
    }

    // フィルタ・ソート
    var beforeCount = results.length;
    results = results.filter(function(r) { return r.followerCount != null && r.followerCount >= minFollowers; });
    results.sort(function(a, b) { return (b.followerCount || 0) - (a.followerCount || 0); });
    appendJobLog_(jobId, 'フィルタ後: ' + results.length + ' 人（' + (beforeCount - results.length) + ' 人除外）');

    // 結果保存
    updateJobField_(jobId, 10, JSON.stringify(results));
    updateJobField_(jobId, 2, 'done');
    appendJobLog_(jobId, '完了！ ' + results.length + ' 人');

    // 利用履歴記録
    logUsage_(postUrl, mode, minFollowers, results.length, rowData[3]);

    return { status: 'done', count: results.length };

  } catch(e) {
    updateJobField_(jobId, 2, 'error');
    updateJobField_(jobId, 9, e.message);
    appendJobLog_(jobId, 'エラー: ' + e.message);
    throw e;
  }
}

// ============================================================
// 利用履歴
// ============================================================

function logUsage_(postUrl, mode, minFollowers, count, accountName) {
  const ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  let sheet = ss.getSheetByName(SHEET_LOG);
  if (!sheet) {
    sheet = ss.insertSheet(SHEET_LOG);
    sheet.appendRow(['実行日時', '実行者メール', 'アカウント名', '投稿URL', 'モード', '最低フォロワー数', '抽出件数']);
    sheet.getRange(1, 1, 1, 7).setFontWeight('bold').setBackground('#4a90d9').setFontColor('#fff');
    sheet.setFrozenRows(1);
  }

  var email = '';
  try { email = Session.getActiveUser().getEmail(); } catch(e) { email = '（取得不可）'; }

  var modeLabels = {
    'like_only': 'いいねのみ',
    'comment_only': 'コメントのみ',
    'both_required': 'いいね＋コメント'
  };

  sheet.appendRow([
    new Date(),
    email,
    accountName || '',
    postUrl,
    modeLabels[mode] || mode,
    minFollowers,
    count
  ]);
}
