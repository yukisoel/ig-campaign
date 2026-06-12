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

#### デプロイ手順

1. https://share.streamlit.io/ にアクセスし、GitHubアカウントでサインイン
2. 「Create app」→「Deploy a public app from GitHub」を選択
3. リポジトリ: `yukisoel/ig-campaign`、Branch: `main`、Main file path: `app.py`
4. 「Advanced settings」→ Python version: `3.11`
5. 「Secrets」エディタに `.streamlit/secrets.toml.example` の内容を実値で貼り付け
6. 「Deploy!」をクリック → 初回ビルドに3〜5分

#### 運用上の重要な注意

**揮発性ストレージ**: Streamlit Cloud のコンテナは再起動するたびにファイルが消えます。
- `accounts.json` → 消えるが、起動時に Secrets から自動再読込されるので **アカウントは必ず Secrets に書く**
- `jobs.json` → 消える。実行中ジョブのチェックポイントも失われる
- `output/` の CSV → 消える。完了したら速やかにダウンロードする運用

**休眠（スリープ）**:
- 約7日間アクセスがないとアプリが休眠状態になる
- 休眠中はジョブも止まる。誰かがURLを開けば自動復帰する
- 長時間ジョブ（30分以上）を回す場合は、念のため別タブで起動URLを開いたままにしておくと安心

**再起動でジョブが中断された場合**:
- 「ジョブ一覧」タブから `interrupted` 状態のジョブを選んで「再開」ボタン
- チェックポイント（取得済みID）を引き継いで続きから走る

**Public 公開の注意**:
- URLを知っていれば誰でもアクセス可能
- Secrets に書いたIGセッションIDがUI操作で間接的に流出するリスクは低いが、**URLは関係者のみに共有する**
- より厳格に制限したい場合は、後から Settings →「Sharing」で Private 化（メールアドレスでアクセス制限）に切替可

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
