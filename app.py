"""
IGキャンペーン候補抽出ツール（マルチアカウント対応）
アカウントの追加・パスワード変更・履歴管理をUIから操作可能
"""
import csv
import io
import json
import os
import time
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv
from instagrapi import Client
from instagrapi.exceptions import BadPassword, LoginRequired, TwoFactorRequired

load_dotenv()

ACCOUNTS_FILE = "accounts.json"

# ---------------------------------------------------------------------------
# アカウントファイル管理
# ---------------------------------------------------------------------------

def load_data() -> dict:
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # ローカルファイルがない場合は Streamlit Secrets から読む（Cloud デプロイ用）
    try:
        accounts = [dict(a) for a in st.secrets.get("accounts", [])]
        return {"accounts": accounts, "password_history": []}
    except Exception:
        return {"accounts": [], "password_history": []}


def save_data(data: dict):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_accounts() -> list[dict]:
    return load_data().get("accounts", [])


def add_account(name: str, username: str, password: str):
    data = load_data()
    data["accounts"].append({"name": name, "username": username, "password": password})
    save_data(data)


def update_password(username: str, new_password: str):
    data = load_data()
    for acc in data["accounts"]:
        if acc["username"] == username:
            # 旧パスワードをログに残す
            data["password_history"].append({
                "username": username,
                "name": acc["name"],
                "old_password": acc["password"],
                "changed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            acc["password"] = new_password
            break
    save_data(data)
    # セッションキャッシュをリセット
    st.session_state.pop(session_key(username), None)
    session_file = f"session_{username}.json"
    if os.path.exists(session_file):
        os.remove(session_file)


def get_password_history() -> list[dict]:
    return load_data().get("password_history", [])


# ---------------------------------------------------------------------------
# ログイン管理
# ---------------------------------------------------------------------------

def session_key(username: str) -> str:
    return f"ig_client_{username}"


def get_client(username: str, password: str) -> Client:
    key = session_key(username)
    if key in st.session_state:
        return st.session_state[key]

    cl = Client()
    cl.delay_range = [1, 3]

    session_file = f"session_{username}.json"
    if os.path.exists(session_file):
        try:
            cl.load_settings(session_file)
            cl.login(username, password)
            st.session_state[key] = cl
            return cl
        except Exception:
            pass

    cl.login(username, password)
    cl.dump_settings(session_file)
    st.session_state[key] = cl
    return cl


def login_with_2fa(username: str, password: str, code: str) -> Client:
    cl = Client()
    cl.delay_range = [1, 3]
    cl.login(username, password, verification_code=code)
    session_file = f"session_{username}.json"
    cl.dump_settings(session_file)
    st.session_state[session_key(username)] = cl
    return cl


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

def get_likers(cl: Client, media_pk: int) -> list[dict]:
    users = cl.media_likers(media_pk)
    return [
        {"user_id": u.pk, "username": u.username, "follower_count": None, "comments": []}
        for u in users
    ]


def get_commenters(cl: Client, media_pk: int) -> list[dict]:
    comments = cl.media_comments(media_pk, amount=5000)
    merged: dict[str, dict] = {}
    for c in comments:
        u = c.user.username
        if u not in merged:
            merged[u] = {"user_id": c.user.pk, "username": u, "follower_count": None, "comments": []}
        merged[u]["comments"].append(c.text)
    return list(merged.values())


def enrich_follower_counts(cl: Client, users: list[dict]) -> list[dict]:
    total = len(users)
    bar = st.progress(0, text=f"フォロワー数を取得中... 0/{total}")
    for i, u in enumerate(users):
        try:
            info = cl.user_info(u["user_id"])
            u["follower_count"] = info.follower_count
        except Exception:
            u["follower_count"] = None
        bar.progress((i + 1) / total, text=f"フォロワー数を取得中... {i+1}/{total}")
    bar.empty()
    return users


# ---------------------------------------------------------------------------
# データ統合・CSV生成
# ---------------------------------------------------------------------------

def merge(likers: list, commenters: list, mode: str) -> list[dict]:
    merged: dict[str, dict] = {}
    if mode in ("like_only", "both_required"):
        for u in likers:
            merged[u["username"]] = u.copy()
    if mode in ("comment_only", "both_required"):
        for u in commenters:
            if u["username"] in merged:
                merged[u["username"]]["comments"] = u["comments"]
            else:
                merged[u["username"]] = u.copy()
    return list(merged.values())


def to_csv(results: list[dict], mode: str) -> bytes:
    output = io.StringIO()
    include_comment = mode in ("comment_only", "both_required")
    fieldnames = ["id_name", "profile_url", "follower_count"]
    if include_comment:
        fieldnames.append("comment")
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in results:
        row = {
            "id_name": "@" + r["username"],
            "profile_url": "https://www.instagram.com/" + r["username"] + "/",
            "follower_count": r["follower_count"] if r["follower_count"] is not None else "",
        }
        if include_comment:
            row["comment"] = "\n".join(r["comments"])
        writer.writerow(row)
    return output.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="IGキャンペーン候補抽出", page_icon="📊", layout="centered")
st.title("📊 IGキャンペーン候補抽出")

MODE_OPTIONS = {
    "いいね＋コメント（どちらかでOK）": "both_required",
    "いいねのみ": "like_only",
    "コメントのみ": "comment_only",
}

tab_main, tab_accounts = st.tabs(["📊 抽出ツール", "⚙️ アカウント管理"])

# ===========================================================================
# タブ2: アカウント管理（st.stop() より前に描画する必要があるため先に処理）
# ===========================================================================
with tab_accounts:
    st.subheader("アカウント一覧")
    accounts = get_accounts()

    if accounts:
        for acc in accounts:
            st.text(f"　{acc['name']}　（@{acc['username']}）")
    else:
        st.info("登録済みアカウントはありません")

    st.divider()

    # --- 新規アカウント追加 ---
    st.subheader("➕ 新規アカウント追加")
    with st.form("add_account_form"):
        new_name = st.text_input("表示名（社内用）", placeholder="例：クライアントA")
        new_username = st.text_input("Instagramユーザー名", placeholder="例：ig_account_name")
        new_password = st.text_input("パスワード", type="password")
        add_submitted = st.form_submit_button("追加する", type="primary")

    if add_submitted:
        if not new_name or not new_username or not new_password:
            st.error("すべての項目を入力してください")
        else:
            existing_usernames = [a["username"] for a in get_accounts()]
            if new_username in existing_usernames:
                st.error(f"@{new_username} はすでに登録されています")
            else:
                add_account(new_name, new_username, new_password)
                st.success(f"@{new_username}（{new_name}）を追加しました")
                st.rerun()

    st.divider()

    # --- パスワード変更 ---
    st.subheader("🔑 パスワード変更")
    accounts = get_accounts()
    if accounts:
        with st.form("update_password_form"):
            target_name = st.selectbox("変更するアカウント", [a["name"] for a in accounts])
            new_pw = st.text_input("新しいパスワード", type="password")
            new_pw_confirm = st.text_input("新しいパスワード（確認）", type="password")
            pw_submitted = st.form_submit_button("パスワードを変更する")

        if pw_submitted:
            if not new_pw:
                st.error("パスワードを入力してください")
            elif new_pw != new_pw_confirm:
                st.error("パスワードが一致しません")
            else:
                target = next(a for a in accounts if a["name"] == target_name)
                update_password(target["username"], new_pw)
                st.success(f"{target_name} のパスワードを更新しました（旧パスワードは履歴に保存済み）")
                st.rerun()
    else:
        st.info("アカウントを先に追加してください")

    st.divider()

    # --- パスワード変更履歴 ---
    st.subheader("📋 パスワード変更履歴")
    history = get_password_history()
    if history:
        for h in reversed(history):
            st.text(f"{h['changed_at']}　{h['name']}（@{h['username']}）　旧: {h['old_password']}")
    else:
        st.info("変更履歴はありません")

# ===========================================================================
# タブ1: 抽出ツール
# ===========================================================================
with tab_main:
    accounts = get_accounts()

    if not accounts:
        st.warning("アカウントが登録されていません。「アカウント管理」タブから追加してください。")
        st.stop()

    st.divider()
    account_names = [a["name"] for a in accounts]
    selected_name = st.selectbox("使用するアカウント", account_names)
    selected = next(a for a in accounts if a["name"] == selected_name)
    username = selected["username"]
    password = selected["password"]

    # ログイン
    key = session_key(username)
    if key not in st.session_state:
        with st.spinner(f"{selected_name} にログイン中..."):
            try:
                get_client(username, password)
                st.rerun()
            except TwoFactorRequired:
                st.warning("2段階認証が必要です")
                code = st.text_input("認証コード（SMS / 認証アプリ）", max_chars=6)
                if st.button("認証する") and code:
                    try:
                        login_with_2fa(username, password, code)
                        st.rerun()
                    except Exception as e:
                        st.error(f"認証失敗: {e}")
                st.stop()
            except BadPassword:
                st.error(f"パスワードが正しくありません。「アカウント管理」タブでパスワードを更新してください。")
                st.stop()
            except Exception as e:
                st.error(f"ログインエラー: {e}")
                st.stop()

    st.success(f"@{username} でログイン済み ✅")
    st.divider()

    with st.form("main_form"):
        post_url = st.text_input("投稿URL", placeholder="https://www.instagram.com/p/XXXXXXXXX/")
        mode_label = st.selectbox("抽出モード", list(MODE_OPTIONS.keys()))
        min_followers = st.number_input("最低フォロワー数（これ未満は除外）", min_value=0, value=1000, step=500)
        submitted = st.form_submit_button("抽出開始", type="primary", use_container_width=True)

    if submitted:
        if not post_url.strip():
            st.error("投稿URLを入力してください")
            st.stop()
        if "/p/" not in post_url:
            st.error("投稿URL（/p/ を含むURL）を入力してください")
            st.stop()

        mode = MODE_OPTIONS[mode_label]
        cl = get_client(username, password)
        likers, commenters = [], []

        with st.status("データ取得中...", expanded=True) as status:
            try:
                st.write("⏳ 投稿を確認中...")
                media_pk = cl.media_pk_from_url(post_url.strip())

                if mode in ("like_only", "both_required"):
                    st.write("⏳ いいね取得中...")
                    likers = get_likers(cl, media_pk)
                    st.write(f"✅ いいね: {len(likers)} 件")

                if mode in ("comment_only", "both_required"):
                    st.write("⏳ コメント取得中...")
                    commenters = get_commenters(cl, media_pk)
                    st.write(f"✅ コメント: {len(commenters)} ユーザー")

                st.write("⏳ データ統合中...")
                merged = merge(likers, commenters, mode)

                st.write(f"⏳ フォロワー数を取得中（{len(merged)} 人）...")
                merged = enrich_follower_counts(cl, merged)

                before = len(merged)
                merged = [r for r in merged if r["follower_count"] is not None and r["follower_count"] >= min_followers]
                merged.sort(key=lambda r: r["follower_count"] or 0, reverse=True)
                st.write(f"✅ フィルタ後: {len(merged)} 人（{before - len(merged)} 人を除外）")

                status.update(label="完了！", state="complete")

            except LoginRequired:
                st.session_state.pop(key, None)
                status.update(label="セッション切れ", state="error")
                st.error("セッションが切れました。ページを再読み込みしてください。")
                st.stop()
            except Exception as e:
                status.update(label="エラーが発生しました", state="error")
                st.error(f"エラー: {e}")
                st.stop()

        st.success(f"抽出完了：**{len(merged)} 人**（フォロワー{int(min_followers):,}人以上）")
        csv_bytes = to_csv(merged, mode)
        filename = "ig_campaign_" + time.strftime("%Y%m%d_%H%M%S") + ".csv"
        st.download_button(
            label="📥 CSVダウンロード",
            data=csv_bytes,
            file_name=filename,
            mime="text/csv",
            type="primary",
            use_container_width=True,
        )

