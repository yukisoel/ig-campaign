# デプロイ手順 — IGキャンペーン候補抽出ツール

サーバー（Streamlit Community Cloud / EC2 / VPS など）で起動するための手順です。

---

## 1. 前提

- Python 3.11 推奨
- Git
- Apify アカウント（有料プラン or 無料 $5 枠）

---

## 2. セットアップ

```bash
git clone git@github.com:yukisoel/ig-campaign.git
cd ig-campaign

# 仮想環境作成 & 依存インストール
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 3. 設定ファイル

### 3-1. `.env`

リポジトリ直下に `.env` を作成。`.env.example` を参考に。

```env
APIFY_TOKEN=apify_api_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

> Apify Token は Apify Console → Settings → Integrations → API tokens から取得。

### 3-2. `accounts.json`

Instagram のアカウント情報。初回は空でOK（UIから追加可能）。

```json
{ "accounts": [] }
```

または Streamlit Cloud にデプロイする場合は `.streamlit/secrets.toml` 経由で設定可。`.streamlit/secrets.toml.example` を参照。

---

## 4. 起動

### ローカル / VPS の場合

```bash
streamlit run app.py --server.port 8501 --server.headless true
```

ブラウザで `http://<サーバーIP>:8501` を開く。

### バックグラウンド常駐（systemd 例）

`/etc/systemd/system/ig-campaign.service`:

```ini
[Unit]
Description=IG Campaign Streamlit
After=network.target

[Service]
WorkingDirectory=/opt/ig-campaign
ExecStart=/opt/ig-campaign/.venv/bin/streamlit run app.py --server.port 8501 --server.headless true
Restart=always
User=ubuntu

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now ig-campaign
```

### Streamlit Community Cloud

1. GitHub リポジトリを連携
2. `app.py` をエントリポイントに指定
3. Secrets に `APIFY_TOKEN` および `accounts` 配列を登録（`.streamlit/secrets.toml.example` 参照）

---

## 5. Streamlit のバージョン注意

`requirements.txt` で `streamlit>=1.35.0` としているが、**1.57.x は uvicorn 切り替えに伴う MIME タイプバグ**でJSが読み込めない不具合あり。トラブった場合は以下で固定:

```bash
pip install "streamlit==1.50.0"
```

---

## 6. 永続化されるファイル

| ファイル / ディレクトリ | 内容 | バックアップ要否 |
|---|---|---|
| `accounts.json` | Instagram アカウント情報（セッションID含む） | ✅ 要 |
| `jobs.json` | ジョブ履歴・チェックポイント | △ ジョブ復元に必要 |
| `session_*.json` | instagrapi 内部セッション | 自動再生成可 |
| `output/` | CSV 出力先 | △ |

これらは `.gitignore` で除外されているのでサーバー側の永続ボリュームに置く。

---

## 7. 動作確認

1. ブラウザで起動URLにアクセス
2. 「アカウント管理」タブから IG アカウント追加（セッションID取得手順はUI内に記載）
3. 「抽出ツール」タブで投稿URLを入力 → 「抽出開始」
4. 「ジョブ一覧」で進捗確認、完了したらCSVダウンロード

---

## 8. トラブルシューティング

| 症状 | 原因 / 対処 |
|---|---|
| 画面が真っ白 | Streamlit 1.57 のMIMEバグ → `pip install "streamlit==1.50.0"` |
| `APIFY_TOKENが設定されていません` | `.env` が読み込めていない or キー未設定 |
| `セッションIDが無効です` | IGのセッションID再取得（UI内の手順参照） |
| ジョブが `interrupted` 状態 | サーバー再起動による中断。UIから「再開」ボタンで継続可能 |
| Apify 料金エラー | Apify ダッシュボードで残枠確認、必要に応じてプランUP |

---

## 9. コスト目安（Apify）

- いいね＋コメントモード: 1回あたり **約 $1.40**（datadoping/instagram-likes-scraper）
- コメントのみモード: **$0**（Apify不使用）
- Starterプラン（$29/月）で約 **20回/月** の実行が可能

---

## 連絡先

ツール改修・不具合報告: SOEL株式会社 武田（takeda@soel-tokyo.jp）
