# Instagram Graph API セットアップ手順

## 前提条件の確認

### Instagramアカウントの種類を確認
- **ビジネスアカウント** または **クリエイターアカウント** が必要
- 個人アカウントの場合はプロアカウントに切り替える
  - Instagram → 設定 → アカウント → プロアカウントに切り替える

### FacebookページとInstagramを連携
- FacebookページにInstagramアカウントを接続する（未接続の場合）
  - Facebookページ → 設定 → Instagram → アカウントを接続

---

## Step 1: Meta Developersでアプリを作成

1. https://developers.facebook.com/ にアクセス
2. 右上「マイアプリ」→「アプリを作成」
3. アプリタイプ: **「その他」** を選択 → 次へ
4. アプリ名を入力（例: `campaign-scraper`）→「アプリを作成」

---

## Step 2: Instagram Graph APIを追加

1. アプリダッシュボード左メニュー「製品を追加」
2. 「Instagram」→「設定」をクリック
3. 左メニュー「Instagram」→「APIの設定」

---

## Step 3: Facebookページ経由でアクセストークンを取得

### 3-1. Graph API エクスプローラーを開く
- https://developers.facebook.com/tools/explorer/

### 3-2. 権限を設定
以下の権限にチェックを入れる：
- `instagram_basic`
- `instagram_manage_insights`
- `pages_show_list`
- `pages_read_engagement`

### 3-3. アクセストークンを生成
1. 「アクセストークンを生成」ボタンをクリック
2. Instagramアカウントへのアクセスを許可
3. 表示されたトークンをコピー

### 3-4. 長期トークンに変換（90日間有効）
ターミナルで実行（YOUR_APP_ID, YOUR_APP_SECRET, YOUR_SHORT_TOKEN を置き換え）:
```bash
curl "https://graph.facebook.com/oauth/access_token?grant_type=fb_exchange_token&client_id=YOUR_APP_ID&client_secret=YOUR_APP_SECRET&fb_exchange_token=YOUR_SHORT_TOKEN"
```

---

## Step 4: .envファイルに設定

```env
IG_ACCESS_TOKEN=取得したアクセストークンをここに貼る
```

---

## Step 5: 動作確認

```bash
python scraper_graph_api.py https://www.instagram.com/p/XXXXX/
```

---

## トラブルシューティング

| エラー | 原因 | 対処 |
|--------|------|------|
| `OAuthException` | トークン期限切れ | Step 3からトークンを再取得 |
| `permissions` エラー | 権限不足 | Step 3-2で権限を再確認 |
| 投稿が見つからない | 非ビジネスアカウント | プロアカウントに切り替え |
