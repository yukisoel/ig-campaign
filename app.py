"""
IGキャンペーン候補抽出ツール（マルチアカウント・ジョブキュー対応）
バックグラウンド処理で複数ユーザーが同時実行可能
"""
import csv
import io
import json
import os
import threading
import time
import uuid
from datetime import datetime

import requests
import streamlit as st
from dotenv import load_dotenv
from instagrapi import Client
from instagrapi.exceptions import LoginRequired

import notify
import storage

load_dotenv()

NOTIFY_DOMAIN = "soel-tokyo.jp"

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
ACCOUNTS_FILE = "accounts.json"
JOBS_FILE = "jobs.json"
OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

@st.cache_resource
def _get_locks_and_caches():
    """Streamlitのrerunで再生成されないようキャッシュ。全スレッドで同じLockを共有する。"""
    return {
        "jobs_lock": threading.Lock(),
        "client_lock": threading.Lock(),
        "client_cache": {},
    }


_shared = _get_locks_and_caches()
_jobs_lock: threading.Lock = _shared["jobs_lock"]
_client_lock: threading.Lock = _shared["client_lock"]
_client_cache: dict[str, Client] = _shared["client_cache"]

# ---------------------------------------------------------------------------
# アカウントファイル管理
# ---------------------------------------------------------------------------

def load_data() -> dict:
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    try:
        accounts = [dict(a) for a in st.secrets.get("accounts", [])]
        return {"accounts": accounts}
    except Exception:
        return {"accounts": []}


def save_data(data: dict):
    with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    storage.push(ACCOUNTS_FILE, data, sync=True)


def get_accounts() -> list[dict]:
    return load_data().get("accounts", [])


def add_account(name: str, username: str, sessionid: str):
    data = load_data()
    data["accounts"].append({"name": name, "username": username, "sessionid": sessionid})
    save_data(data)


def update_sessionid(username: str, new_sessionid: str):
    data = load_data()
    for acc in data["accounts"]:
        if acc["username"] == username:
            acc["sessionid"] = new_sessionid
            break
    save_data(data)
    with _client_lock:
        _client_cache.pop(f"ig_client_{username}", None)
    session_file = f"session_{username}.json"
    if os.path.exists(session_file):
        os.remove(session_file)


def delete_account(username: str):
    data = load_data()
    data["accounts"] = [a for a in data["accounts"] if a["username"] != username]
    save_data(data)
    with _client_lock:
        _client_cache.pop(f"ig_client_{username}", None)
    session_file = f"session_{username}.json"
    if os.path.exists(session_file):
        os.remove(session_file)


# ---------------------------------------------------------------------------
# ログイン管理（スレッドセーフ・モジュールレベルキャッシュ）
# ---------------------------------------------------------------------------

def _patch_session_timeout(session, timeout: float = 30.0):
    """requests.Session.request にデフォルトタイムアウトを注入。
    instagrapi 内部の全HTTPリクエストが指定秒で打ち切られるようになる。"""
    original_request = session.request

    def request_with_timeout(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        return original_request(method, url, **kwargs)

    session.request = request_with_timeout


def _configure_client(cl: Client):
    """instagrapi の内部セッションにタイムアウトを設定。"""
    _patch_session_timeout(cl.private, timeout=30.0)
    _patch_session_timeout(cl.public, timeout=30.0)


def get_client(username: str, sessionid: str) -> Client:
    key = f"ig_client_{username}"
    with _client_lock:
        if key in _client_cache:
            return _client_cache[key]

        session_file = f"session_{username}.json"

        if os.path.exists(session_file):
            try:
                cl = Client()
                cl.delay_range = [3, 8]
                _configure_client(cl)
                cl.load_settings(session_file)
                cl.login_by_sessionid(sessionid)
                cl.dump_settings(session_file)
                _client_cache[key] = cl
                return cl
            except Exception:
                try:
                    os.remove(session_file)
                except Exception:
                    pass

        cl = Client()
        cl.delay_range = [3, 8]
        _configure_client(cl)
        cl.login_by_sessionid(sessionid)
        cl.dump_settings(session_file)
        _client_cache[key] = cl
        return cl


# ---------------------------------------------------------------------------
# ジョブ管理
# ---------------------------------------------------------------------------

def _load_jobs_raw() -> list[dict]:
    if not os.path.exists(JOBS_FILE):
        return []
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        # 壊れたファイルはタイムスタンプ付きでバックアップして、空リストで再開
        try:
            backup = f"{JOBS_FILE}.broken_{int(time.time())}"
            os.rename(JOBS_FILE, backup)
        except Exception:
            pass
        return []


def _save_jobs_raw(jobs: list[dict], sync: bool = False):
    """アトミック書き込み: tmpに書いてからrenameする。書き込み中のファイルを読まれて壊れるのを防ぐ。
    sync=Trueなら GitHub への push も即時実行（再起動を超えて確実に残したい状態遷移用）。"""
    tmp = JOBS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, JOBS_FILE)
    storage.push(JOBS_FILE, jobs, sync=sync)


def load_jobs() -> list[dict]:
    with _jobs_lock:
        return _load_jobs_raw()


def add_job(params: dict) -> str:
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    job = {
        "id": job_id,
        "status": "waiting",
        "created_at": datetime.now().isoformat(),
        "params": params,
        "log": [],
        "result_path": None,
        "error": None,
        "checkpoint": {},
    }
    with _jobs_lock:
        jobs = _load_jobs_raw()
        jobs.append(job)
        _save_jobs_raw(jobs, sync=True)
    return job_id


def update_job(job_id: str, log_append: str | None = None, **kwargs):
    # 終了状態（done/error/interrupted/cancelled）の遷移は GitHub にも即時反映
    sync = kwargs.get("status") in ("done", "error", "interrupted", "cancelled")
    with _jobs_lock:
        jobs = _load_jobs_raw()
        for job in jobs:
            if job["id"] == job_id:
                if log_append is not None:
                    job["log"].append(log_append)
                for k, v in kwargs.items():
                    job[k] = v
                break
        _save_jobs_raw(jobs, sync=sync)


def delete_job(job_id: str):
    with _jobs_lock:
        jobs = _load_jobs_raw()
        jobs = [j for j in jobs if j["id"] != job_id]
        _save_jobs_raw(jobs, sync=True)


# ---------------------------------------------------------------------------
# Apify ユーティリティ
# ---------------------------------------------------------------------------

def _apify_start_actor(actor_id: str, run_input: dict) -> str:
    """Apify Actorを起動し、run IDを返す。"""
    api_actor_id = actor_id.replace("/", "~")
    res = requests.post(
        f"https://api.apify.com/v2/acts/{api_actor_id}/runs",
        headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
        json=run_input,
        timeout=30,
    )
    res.raise_for_status()
    return res.json()["data"]["id"]


def _apify_wait_for_run(run_id: str, max_sec: int = 300, job_id: str | None = None) -> dict:
    """Apify Runの完了を待ち、結果を返す。"""
    deadline = time.time() + max_sec
    while time.time() < deadline:
        time.sleep(5)
        if job_id:
            _check_cancelled(job_id)
        res = requests.get(
            f"https://api.apify.com/v2/actor-runs/{run_id}",
            headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
            timeout=15,
        )
        res.raise_for_status()
        run = res.json()["data"]
        if run["status"] == "SUCCEEDED":
            return run
        if run["status"] in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"Apify実行失敗: {run['status']}")
    raise RuntimeError("Apifyタイムアウト。再度お試しください。")


def _apify_get_dataset(dataset_id: str) -> list[dict]:
    """Apify Datasetからアイテムを取得する。"""
    res = requests.get(
        f"https://api.apify.com/v2/datasets/{dataset_id}/items",
        headers={"Authorization": f"Bearer {APIFY_TOKEN}"},
        params={"limit": 10000},
        timeout=30,
    )
    res.raise_for_status()
    return res.json()


# ---------------------------------------------------------------------------
# データ取得
# ---------------------------------------------------------------------------

def get_likers_apify(post_url: str, session_id: str, job_id: str) -> list[dict]:
    """複数のApify Actorを併用していいねユーザーを最大限取得。"""
    results = []
    seen = set()

    def _add_items(items):
        for item in items:
            username = item.get("username") or ""
            if not username or username in seen:
                continue
            seen.add(username)
            results.append({
                "user_id": item.get("id") or item.get("pk"),
                "username": username,
                "follower_count": None,
                "comments": [],
            })

    # 1. datadoping（認証不要、最大約1000件）
    update_job(job_id, log_append="  [1/2] datadopingで取得中...")
    run_id = _apify_start_actor("datadoping/instagram-likes-scraper", {
        "posts": [post_url],
        "max_count": 1000,
    })
    update_job(job_id, log_append=f"  RunID: {run_id}")
    run = _apify_wait_for_run(run_id, max_sec=600, job_id=job_id)
    items1 = _apify_get_dataset(run["defaultDatasetId"])
    _add_items(items1)
    update_job(job_id, log_append=f"  datadoping: {len(items1)} 件取得（ユニーク: {len(results)}）")

    # 2. scrapier（セッションID認証、追加分を取得）
    _check_cancelled(job_id)
    update_job(job_id, log_append="  [2/2] scrapierで追加取得中...")
    try:
        run_id2 = _apify_start_actor("scrapier/instagram-likes-scraper", {
            "startUrls": [post_url],
            "maxCount": 10000,
            "sessionId": session_id,
        })
        update_job(job_id, log_append=f"  RunID: {run_id2}")
        run2 = _apify_wait_for_run(run_id2, max_sec=600, job_id=job_id)
        items2 = _apify_get_dataset(run2["defaultDatasetId"])
        before = len(results)
        _add_items(items2)
        update_job(job_id, log_append=f"  scrapier: {len(items2)} 件取得（新規: {len(results) - before}）")
    except Exception as e:
        update_job(job_id, log_append=f"  scrapier: スキップ（{e}）")

    return results


def get_commenters(cl: Client, media_pk: int) -> list[dict]:
    comments = cl.media_comments(media_pk, amount=5000)
    merged: dict[str, dict] = {}
    for c in comments:
        u = c.user.username
        if u not in merged:
            merged[u] = {"user_id": c.user.pk, "username": u, "follower_count": None, "comments": []}
        merged[u]["comments"].append(c.text)
    return list(merged.values())


class JobCancelled(Exception):
    pass


def _check_cancelled(job_id: str):
    jobs = load_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if job and job["status"] == "cancelled":
        raise JobCancelled()


def enrich_follower_counts(cl: Client, users: list[dict], job_id: str, checkpoint: dict | None = None) -> list[dict]:
    """フォロワー数をusersに書き込む。checkpointを渡すと50人ごとに保存して途中再開可能にする。
    各 user_info はinstagrapi内部のHTTPタイムアウト（30秒）で打ち切られる。
    連続10回失敗したら中断（Instagramレートリミットの可能性）。"""
    total = len(users)
    done = sum(1 for u in users if u["follower_count"] is not None)
    SAVE_EVERY = 50
    MAX_CONSECUTIVE_FAILURES = 10
    consecutive_failures = 0

    for u in users:
        if u["follower_count"] is not None:
            continue
        try:
            info = cl.user_info(u["user_id"])
            u["follower_count"] = info.follower_count
            consecutive_failures = 0
        except Exception:
            u["follower_count"] = None
            consecutive_failures += 1
        done += 1

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            if checkpoint is not None:
                checkpoint["merged"] = users
                update_job(job_id, checkpoint=checkpoint)
            raise RuntimeError(
                f"フォロワー数取得が連続{MAX_CONSECUTIVE_FAILURES}回失敗しました（{done}/{total} 人時点）。"
                f"Instagramのレートリミットの可能性があります。しばらく待ってから再開してください。"
            )

        if done % 10 == 0:
            _check_cancelled(job_id)
        if done % SAVE_EVERY == 0 or done == total:
            if checkpoint is not None:
                checkpoint["merged"] = users
                update_job(job_id, checkpoint=checkpoint, log_append=f"  フォロワー数取得中: {done}/{total}")
            else:
                update_job(job_id, log_append=f"  フォロワー数取得中: {done}/{total}")
    return users


# ---------------------------------------------------------------------------
# データ統合・CSV生成
# ---------------------------------------------------------------------------

def merge(likers: list, commenters: list, mode: str) -> list[dict]:
    merged: dict[str, dict] = {}
    if mode == "both_required":
        for u in likers:
            merged[u["username"]] = u.copy()
    for u in commenters:
        if u["username"] in merged:
            merged[u["username"]]["comments"] = u["comments"]
        else:
            merged[u["username"]] = u.copy()
    return list(merged.values())


def to_csv(results: list[dict]) -> bytes:
    output = io.StringIO()
    fieldnames = ["id_name", "profile_url", "follower_count", "comment"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for r in results:
        writer.writerow({
            "id_name": "@" + r["username"],
            "profile_url": "https://www.instagram.com/" + r["username"] + "/",
            "follower_count": r["follower_count"] if r["follower_count"] is not None else "",
            "comment": "\n".join(r["comments"]),
        })
    return output.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# ジョブワーカー（バックグラウンドスレッド）
# ---------------------------------------------------------------------------

def _save_checkpoint(job_id: str, checkpoint: dict, **kwargs):
    """チェックポイント保存。kwargsは update_job にそのまま渡す。"""
    update_job(job_id, checkpoint=checkpoint, **kwargs)


def _notify_terminal(job_id: str):
    """ジョブ終了（done/error）時にメール通知。失敗してもジョブ本体は止めない。"""
    try:
        if not notify.enabled():
            return
        job = next((j for j in load_jobs() if j["id"] == job_id), None)
        if not job:
            return
        emails = job["params"].get("notify_emails") or []
        if not emails:
            return
        p = job["params"]
        status = job["status"]
        post_url = p.get("post_url", "")
        if status == "done":
            subject = f"[IG抽出] 完了: {p['account_name']}"
            body = (
                f"IGキャンペーン候補抽出が完了しました。\n\n"
                f"アカウント: {p['account_name']} (@{p['username']})\n"
                f"投稿URL: {post_url}\n"
                f"モード: {MODE_LABELS.get(p['mode'], p['mode'])}\n"
                f"最低フォロワー: {p['min_followers']:,}\n"
                f"ジョブID: {job['id']}\n\n"
                f"結果CSVを添付しています。\n"
            )
            attachments = []
            result_path = job.get("result_path")
            if result_path and os.path.exists(result_path):
                with open(result_path, "rb") as f:
                    attachments.append((os.path.basename(result_path), f.read(), "text/csv"))
            notify.send(emails, subject, body, attachments=attachments or None)
        elif status == "error":
            subject = f"[IG抽出] エラー: {p['account_name']}"
            log_tail = "\n".join(job.get("log", [])[-30:])
            body = (
                f"IGキャンペーン候補抽出でエラーが発生しました。\n\n"
                f"アカウント: {p['account_name']} (@{p['username']})\n"
                f"投稿URL: {post_url}\n"
                f"ジョブID: {job['id']}\n"
                f"エラー: {job.get('error') or '（詳細なし）'}\n\n"
                f"--- ログ末尾 ---\n{log_tail}\n\n"
                f"ツールの「ジョブ一覧」から「再開」ボタンでチェックポイントから続行できる場合があります。\n"
            )
            notify.send(emails, subject, body)
    except Exception as e:
        print(f"[notify] _notify_terminal failed: {e}")


def run_job(job_id: str):
    jobs = load_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return

    params = job["params"]
    checkpoint = job.get("checkpoint") or {}
    is_resume = bool(checkpoint)
    update_job(job_id, status="running", error=None)

    try:
        if is_resume:
            saved_keys = [k for k in ("media_pk", "likers", "commenters", "merged") if k in checkpoint]
            update_job(job_id, log_append=f"🔄 再開: 保存済みデータ={saved_keys}")

        # ---- ログイン（毎回必要。リダイレクトエラーはセッションID無効） ----
        update_job(job_id, log_append="⏳ ログイン中...")
        try:
            cl = get_client(params["username"], params["sessionid"])
        except Exception as login_err:
            if "redirect" in str(login_err).lower():
                with _client_lock:
                    _client_cache.pop(f"ig_client_{params['username']}", None)
                session_file = f"session_{params['username']}.json"
                if os.path.exists(session_file):
                    os.remove(session_file)
                raise LoginRequired("セッションIDが無効です。アカウント管理からセッションIDを再取得・更新してください。")
            raise
        update_job(job_id, log_append=f"✅ @{params['username']} でログイン完了")

        # ---- media_pk ----
        if "media_pk" in checkpoint:
            media_pk = checkpoint["media_pk"]
            update_job(job_id, log_append="♻️ 投稿情報: 保存済みを再利用")
        else:
            update_job(job_id, log_append="⏳ 投稿を確認中...")
            media_pk = cl.media_pk_from_url(params["post_url"])
            checkpoint["media_pk"] = media_pk
            _save_checkpoint(job_id, checkpoint, log_append="✅ 投稿確認完了")

        mode = params["mode"]
        likers, commenters = [], []

        # ---- いいね取得 ----
        if mode == "both_required":
            if "likers" in checkpoint:
                likers = checkpoint["likers"]
                update_job(job_id, log_append=f"♻️ いいね: 保存済み {len(likers)} 件を再利用")
            else:
                update_job(job_id, log_append="⏳ いいね取得中（Apify）...")
                if not APIFY_TOKEN:
                    raise RuntimeError("APIFY_TOKENが設定されていません。.envファイルにAPIFY_TOKEN=xxxを追加してください。")
                likers = get_likers_apify(params["post_url"], params["sessionid"], job_id)
                checkpoint["likers"] = likers
                _save_checkpoint(job_id, checkpoint, log_append=f"✅ いいね: {len(likers)} 件")

        # ---- コメント取得 ----
        if mode in ("comment_only", "both_required"):
            if "commenters" in checkpoint:
                commenters = checkpoint["commenters"]
                update_job(job_id, log_append=f"♻️ コメント: 保存済み {len(commenters)} ユーザーを再利用")
            else:
                update_job(job_id, log_append="⏳ コメント取得中...")
                commenters = get_commenters(cl, media_pk)
                checkpoint["commenters"] = commenters
                _save_checkpoint(job_id, checkpoint, log_append=f"✅ コメント: {len(commenters)} ユーザー")

        # ---- マージ ----
        if "merged" in checkpoint:
            merged = checkpoint["merged"]
        else:
            merged = merge(likers, commenters, mode)
            checkpoint["merged"] = merged
            _save_checkpoint(job_id, checkpoint)

        # ---- フォロワー数取得（部分保存対応） ----
        need_enrich = [u for u in merged if u["follower_count"] is None]
        if need_enrich:
            update_job(job_id, log_append=f"⏳ フォロワー数を取得中（残り {len(need_enrich)} 人 / 全体 {len(merged)} 人）...")
            enrich_follower_counts(cl, merged, job_id, checkpoint)

        # ---- フィルタ・CSV出力 ----
        min_followers = params["min_followers"]
        before = len(merged)
        filtered = [r for r in merged if r["follower_count"] is not None and r["follower_count"] >= min_followers]
        filtered.sort(key=lambda r: r["follower_count"] or 0, reverse=True)
        update_job(job_id, log_append=f"✅ フィルタ後: {len(filtered)} 人（{before - len(filtered)} 人を除外）")

        filename = f"ig_campaign_{job_id}.csv"
        result_path = os.path.join(OUTPUT_DIR, filename)
        with open(result_path, "wb") as f:
            f.write(to_csv(filtered))

        update_job(job_id, status="done", result_path=result_path,
                   log_append=f"✅ 完了！{len(filtered)} 人")
        _notify_terminal(job_id)

    except JobCancelled:
        pass  # ステータスは既にcancelledに更新済み
    except LoginRequired as e:
        with _client_lock:
            _client_cache.pop(f"ig_client_{params['username']}", None)
        msg = str(e) or "セッション切れ。アカウント管理からセッションIDを更新してください。"
        update_job(job_id, status="error", error=msg, log_append=f"❌ {msg}")
        _notify_terminal(job_id)
    except Exception as e:
        update_job(job_id, status="error", error=str(e),
                   log_append=f"❌ エラー: {e}（チェックポイントから再開できます）")
        _notify_terminal(job_id)


def submit_job(params: dict) -> str:
    job_id = add_job(params)
    t = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    t.start()
    return job_id


def resume_job(job_id: str):
    """中断/エラー状態のジョブをチェックポイントから再開する。"""
    update_job(job_id, status="waiting", error=None, log_append="🔄 ジョブを再開します")
    t = threading.Thread(target=run_job, args=(job_id,), daemon=True)
    t.start()


def update_job_account(job_id: str, new_account: dict):
    """ジョブのアカウント情報を差し替える。checkpointは保持。"""
    with _jobs_lock:
        jobs = _load_jobs_raw()
        for job in jobs:
            if job["id"] == job_id:
                job["params"]["account_name"] = new_account["name"]
                job["params"]["username"] = new_account["username"]
                job["params"]["sessionid"] = new_account["sessionid"]
                job["log"].append(
                    f"🔁 アカウントを変更: @{new_account['username']} ({new_account['name']})"
                )
                # キャッシュされた古いClientは使わせない
                break
        _save_jobs_raw(jobs, sync=True)
    with _client_lock:
        # 新アカウントでログインし直すよう、古いキャッシュをクリア
        _client_cache.clear()


def export_partial_csv(job_id: str) -> tuple[str | None, int]:
    """checkpointの現在のmergedからCSV出力する。
    返り値: (生成したCSVのパス or None, 出力件数)"""
    jobs = load_jobs()
    job = next((j for j in jobs if j["id"] == job_id), None)
    if not job:
        return None, 0
    cp = job.get("checkpoint") or {}
    merged = cp.get("merged")
    if not merged:
        return None, 0

    params = job["params"]
    min_followers = params["min_followers"]

    filtered = [r for r in merged if r["follower_count"] is not None and r["follower_count"] >= min_followers]
    filtered.sort(key=lambda r: r["follower_count"] or 0, reverse=True)

    filename = f"ig_campaign_{job_id}_partial.csv"
    result_path = os.path.join(OUTPUT_DIR, filename)
    with open(result_path, "wb") as f:
        f.write(to_csv(filtered))
    return result_path, len(filtered)


# ---------------------------------------------------------------------------
# 起動時: GitHubから状態を復元 → 中断されたジョブをクリーンアップ（1回だけ実行）
# ---------------------------------------------------------------------------
@st.cache_resource
def _startup():
    """サーバープロセス起動時に1回だけ実行（st.cache_resourceで保証）。
    Streamlit Cloud のコンテナ再起動でローカルファイルは消えるため、
    GitHub の永続ストレージから accounts.json / jobs.json を復元する。"""
    storage.hydrate([ACCOUNTS_FILE, JOBS_FILE])
    with _jobs_lock:
        jobs = _load_jobs_raw()
        changed = False
        for job in jobs:
            if job["status"] in ("waiting", "running"):
                job["status"] = "interrupted"
                job["log"].append("⏸ サーバー再起動により中断（再開可能）")
                changed = True
        if changed:
            _save_jobs_raw(jobs, sync=True)

_startup()

# ---------------------------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="IGキャンペーン候補抽出", page_icon="📊", layout="centered")
st.title("📊 IGキャンペーン候補抽出")

MODE_OPTIONS = {
    "いいね＋コメント": "both_required",
    "コメントのみ": "comment_only",
}
MODE_LABELS = {v: k for k, v in MODE_OPTIONS.items()}

STATUS_LABEL = {
    "waiting": "🕐 待機中",
    "running": "⏳ 実行中",
    "done": "✅ 完了",
    "error": "❌ エラー",
    "cancelled": "🛑 停止",
    "interrupted": "⏸ 中断（再開可能）",
}

tab_main, tab_jobs, tab_accounts = st.tabs(["📊 抽出ツール", "📋 ジョブ一覧", "⚙️ アカウント管理"])


# ===========================================================================
# タブ1: 抽出ツール
# ===========================================================================
with tab_main:
    accounts = get_accounts()

    if not accounts:
        st.warning("アカウントが登録されていません。「アカウント管理」タブから追加してください。")
    else:
        st.divider()

        with st.form("main_form"):
            account_names = [a["name"] for a in accounts]
            selected_name = st.selectbox("使用するアカウント", account_names)
            post_url = st.text_input("投稿URL", placeholder="https://www.instagram.com/p/XXXXXXXXX/")
            mode_label = st.selectbox("抽出モード", list(MODE_OPTIONS.keys()))
            min_followers = st.number_input("最低フォロワー数（これ未満は除外）", min_value=0, value=1000, step=500)
            notify_help = (
                f"完了/エラー時にメール通知。複数宛先はカンマ区切り（例: takeda, hanada）。空欄なら通知なし。"
                if notify.enabled()
                else "メール通知は無効（RESEND_API_KEY / RESEND_FROM 未設定）"
            )
            col_email, col_domain = st.columns([2, 1])
            with col_email:
                notify_input = st.text_input(
                    "通知先メール",
                    placeholder="takeda",
                    help=notify_help,
                    disabled=not notify.enabled(),
                )
            with col_domain:
                st.markdown(
                    f"<div style='margin-top: 1.95rem; padding-top: 0.4rem; color: rgba(250,250,250,0.7);'>@{NOTIFY_DOMAIN}</div>",
                    unsafe_allow_html=True,
                )
            submitted = st.form_submit_button("抽出開始", type="primary", use_container_width=True)

        if submitted:
            if not post_url.strip():
                st.error("投稿URLを入力してください")
            elif "/p/" not in post_url:
                st.error("投稿URL（/p/ を含むURL）を入力してください")
            else:
                selected = next(a for a in accounts if a["name"] == selected_name)
                notify_emails: list[str] = []
                for raw in (notify_input or "").split(","):
                    user = raw.strip()
                    if not user:
                        continue
                    notify_emails.append(user if "@" in user else f"{user}@{NOTIFY_DOMAIN}")
                params = {
                    "account_name": selected["name"],
                    "username": selected["username"],
                    "sessionid": selected["sessionid"],
                    "post_url": post_url.strip(),
                    "mode": MODE_OPTIONS[mode_label],
                    "min_followers": int(min_followers),
                    "notify_emails": notify_emails,
                }
                submit_job(params)
                msg = "ジョブを登録しました。「ジョブ一覧」タブで進捗を確認できます。"
                if notify_emails:
                    msg += f"\n\n完了/エラー時に {', '.join(notify_emails)} へメール通知します。"
                st.success(msg)


# ===========================================================================
# タブ3: アカウント管理（ジョブ一覧の自動リフレッシュより先に描画する）
# ===========================================================================
with tab_accounts:
    if storage.enabled():
        st.caption("☁️ ストレージ: GitHub 永続化が有効")
    else:
        st.caption("💾 ストレージ: ローカルファイルのみ（GITHUB_TOKEN / GITHUB_DATA_REPO 未設定）")
    if notify.enabled():
        st.caption("📧 メール通知: 有効（Resend）")
    else:
        st.caption("📭 メール通知: 無効（RESEND_API_KEY / RESEND_FROM 未設定）")
    st.subheader("アカウント一覧")
    accounts = get_accounts()

    if accounts:
        for acc in accounts:
            col1, col2 = st.columns([4, 1])
            col1.text(f"　{acc['name']}　（@{acc['username']}）")
            if col2.button("削除", key=f"del_{acc['username']}"):
                delete_account(acc["username"])
                st.rerun()
    else:
        st.info("登録済みアカウントはありません")

    st.divider()

    with st.expander("❓ セッションIDの取得方法"):
        st.markdown("""
**Chromeでの手順（クライアントに送る手順書）**

1. Chromeで **instagram.com** を開き、対象アカウントでログインする
2. キーボードで **F12**（MacはCmd+Option+I）を押して開発者ツールを開く
3. 上部メニューの **「Application」** タブをクリック
4. 左メニューの **「Cookies」→「https://www.instagram.com」** をクリック
5. 一覧から **「sessionid」** を探し、**「Value」列の値をコピー**する
6. このツールの「セッションID」欄に貼り付ける

> セッションIDは数ヶ月間有効です。期限切れになったら同じ手順で再取得してください。
        """)

    st.divider()

    st.subheader("➕ 新規アカウント追加")
    if "add_form_key" not in st.session_state:
        st.session_state.add_form_key = 0
    with st.form(f"add_account_form_{st.session_state.add_form_key}"):
        new_name = st.text_input("表示名（社内用）", placeholder="例：クライアントA")
        new_username = st.text_input("Instagramユーザー名", placeholder="例：ig_account_name")
        new_sessionid = st.text_input("セッションID", placeholder="ブラウザのCookieから取得した値を貼り付け")
        add_submitted = st.form_submit_button("追加する", type="primary")

    if add_submitted:
        if not new_name or not new_username or not new_sessionid:
            st.error("すべての項目を入力してください")
        else:
            existing_usernames = [a["username"] for a in get_accounts()]
            if new_username in existing_usernames:
                st.error(f"@{new_username} はすでに登録されています")
            else:
                add_account(new_name, new_username, new_sessionid)
                st.session_state.add_form_key += 1
                st.success(f"@{new_username}（{new_name}）を追加しました")
                st.rerun()

    st.divider()

    st.subheader("🔑 セッションID更新")
    accounts = get_accounts()
    if accounts:
        if "update_form_key" not in st.session_state:
            st.session_state.update_form_key = 0
        with st.form(f"update_sessionid_form_{st.session_state.update_form_key}"):
            target_name = st.selectbox("更新するアカウント", [a["name"] for a in accounts])
            new_sid = st.text_input("新しいセッションID", placeholder="ブラウザのCookieから取得した値を貼り付け")
            sid_submitted = st.form_submit_button("更新する")

        if sid_submitted:
            if not new_sid:
                st.error("セッションIDを入力してください")
            else:
                target = next(a for a in accounts if a["name"] == target_name)
                update_sessionid(target["username"], new_sid)
                st.session_state.update_form_key += 1
                st.success(f"{target_name} のセッションIDを更新しました")
                st.rerun()
    else:
        st.info("アカウントを先に追加してください")


# ===========================================================================
# タブ2: ジョブ一覧（自動リフレッシュでst.rerun()するため最後に配置）
# ===========================================================================
with tab_jobs:
    jobs = load_jobs()

    if not jobs:
        st.info("まだジョブがありません。「抽出ツール」タブから実行してください。")
    else:
        jobs_sorted = sorted(jobs, key=lambda j: j["created_at"], reverse=True)

        for job in jobs_sorted:
            status = job["status"]
            label = STATUS_LABEL.get(status, status)
            created = job["created_at"][:16].replace("T", " ")
            p = job["params"]
            post_short = p["post_url"][:50] + ("..." if len(p["post_url"]) > 50 else "")
            title = f"{label}　{created}　{p['account_name']}　{post_short}"

            with st.expander(title, expanded=(status in ("running", "error", "interrupted"))):
                col1, col2 = st.columns([3, 1])
                with col1:
                    st.write(f"**アカウント:** {p['account_name']} (@{p['username']})")
                    st.write(f"**モード:** {MODE_LABELS.get(p['mode'], p['mode'])}　**最低フォロワー:** {p['min_followers']:,}")
                    st.write(f"**URL:** {p['post_url']}")

                if job["log"]:
                    st.code("\n".join(job["log"]), language=None)

                if status == "error" and job.get("error"):
                    st.error(job["error"])

                if status == "done" and job.get("result_path") and os.path.exists(job["result_path"]):
                    with open(job["result_path"], "rb") as f:
                        csv_bytes = f.read()
                    col2.download_button(
                        label="📥 CSV",
                        data=csv_bytes,
                        file_name=os.path.basename(job["result_path"]),
                        mime="text/csv",
                        key=f"dl_{job['id']}",
                        type="primary",
                    )

                if status in ("waiting", "running"):
                    if st.button("停止", key=f"stop_job_{job['id']}", type="secondary"):
                        update_job(job["id"], status="cancelled", log_append="🛑 ユーザーにより停止")
                        st.rerun()

                if status in ("interrupted", "error", "cancelled"):
                    cp = job.get("checkpoint") or {}
                    saved = [k for k in ("likers", "commenters", "merged") if k in cp]
                    if saved:
                        st.caption(f"💾 保存済みデータ: {', '.join(saved)}")

                    # アカウント差し替えUI（レートリミット対策）
                    accounts_now = get_accounts()
                    other_accounts = [a for a in accounts_now if a["username"] != p["username"]]
                    if other_accounts:
                        with st.expander("🔁 アカウントを変更して再開（レートリミット対策）"):
                            choice = st.selectbox(
                                "差し替え後のアカウント",
                                options=[a["name"] for a in other_accounts],
                                key=f"acc_select_{job['id']}",
                            )
                            if st.button("このアカウントに変更", key=f"acc_change_{job['id']}"):
                                target = next(a for a in other_accounts if a["name"] == choice)
                                update_job_account(job["id"], target)
                                st.success(f"アカウントを @{target['username']} に変更しました")
                                st.rerun()

                    if st.button("▶ 再開", key=f"resume_job_{job['id']}", type="primary"):
                        resume_job(job["id"])
                        st.rerun()

                # フォロワー数取得中・中断・エラー時に「ここまでCSV出力」を可能にする
                if status in ("running", "interrupted", "error", "cancelled"):
                    cp = job.get("checkpoint") or {}
                    merged = cp.get("merged") or []
                    enriched_count = sum(1 for u in merged if u.get("follower_count") is not None)
                    partial_path = os.path.join(OUTPUT_DIR, f"ig_campaign_{job['id']}_partial.csv")

                    if enriched_count > 0:
                        if st.button(
                            f"📥 ここまでの結果でCSV生成（{enriched_count}人取得済み）",
                            key=f"partial_csv_{job['id']}",
                        ):
                            export_partial_csv(job["id"])
                            st.rerun()

                    # ファイルが存在すれば常時ダウンロードボタンを表示（自動rerunで消えないように）
                    if os.path.exists(partial_path):
                        with open(partial_path, "rb") as f:
                            csv_bytes_partial = f.read()
                        # ファイル内の行数からおおよその件数を算出
                        line_count = csv_bytes_partial.count(b"\n") - 1  # ヘッダー除く
                        st.success(f"✅ 部分CSV生成済み（{max(line_count, 0):,} 人）")
                        st.download_button(
                            label=f"📥 ダウンロード（{os.path.basename(partial_path)}）",
                            data=csv_bytes_partial,
                            file_name=os.path.basename(partial_path),
                            mime="text/csv",
                            key=f"dl_partial_{job['id']}",
                            type="primary",
                        )

                if status in ("done", "error", "cancelled", "interrupted"):
                    if st.button("削除", key=f"del_job_{job['id']}"):
                        if job.get("result_path") and os.path.exists(job["result_path"]):
                            os.remove(job["result_path"])
                        delete_job(job["id"])
                        st.rerun()

    # 実行中・待機中ジョブがあれば自動リフレッシュ
    if any(j["status"] in ("waiting", "running") for j in jobs):
        time.sleep(2)
        st.rerun()
