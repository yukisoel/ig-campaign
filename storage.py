"""GitHub-backed persistence for Streamlit Cloud.

Streamlit Community Cloud のファイルシステムは揮発性のため、状態ファイル
（accounts.json / jobs.json）を別の GitHub プライベートリポジトリにミラー
してコンテナ再起動を超えて保持する。

- 読み込みはローカルキャッシュから（速い）
- 書き込みはローカルへ即時反映 + GitHub へミラー
- ミラーは sync=True なら即時、False なら3秒デバウンスのバックグラウンド送信
"""
import base64
import json
import os
import threading
import time
from typing import Any

import requests
import streamlit as st


PUSH_INTERVAL = 3.0  # seconds; バックグラウンド送信の周期
HTTP_TIMEOUT = 15


class _GitHubStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending: dict[str, Any] = {}
        self._sha_cache: dict[str, str] = {}
        self._enabled = False
        self._token = ""
        self._repo = ""
        self._initialized = False
        self._flush_thread: threading.Thread | None = None

    def _ensure_init(self) -> bool:
        if self._initialized:
            return self._enabled
        with self._lock:
            if self._initialized:
                return self._enabled
            try:
                self._token = st.secrets.get("GITHUB_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
                self._repo = st.secrets.get("GITHUB_DATA_REPO", "") or os.environ.get("GITHUB_DATA_REPO", "")
            except Exception:
                self._token = os.environ.get("GITHUB_TOKEN", "")
                self._repo = os.environ.get("GITHUB_DATA_REPO", "")
            self._enabled = bool(self._token and self._repo)
            self._initialized = True
            if self._enabled and self._flush_thread is None:
                t = threading.Thread(target=self._flush_loop, daemon=True)
                t.start()
                self._flush_thread = t
        return self._enabled

    @property
    def enabled(self) -> bool:
        return self._ensure_init()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _url(self, path: str) -> str:
        return f"https://api.github.com/repos/{self._repo}/contents/{path}"

    def hydrate(self, paths: list[str]):
        """起動時に GitHub から既存ファイルをローカルへ復元する。
        ローカルに既に存在する場合は上書きしない（ローカル開発を壊さない）。"""
        if not self._ensure_init():
            return
        for path in paths:
            if os.path.exists(path):
                # 既存のSHAを覚えておく（後の更新で衝突を避ける）
                self._sha_cache[path] = self._fetch_sha(path) or ""
                continue
            try:
                r = requests.get(self._url(path), headers=self._headers, timeout=HTTP_TIMEOUT)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                data = r.json()
                content = base64.b64decode(data["content"])
                with open(path, "wb") as f:
                    f.write(content)
                self._sha_cache[path] = data["sha"]
                print(f"[storage] hydrated {path} from {self._repo}")
            except Exception as e:
                print(f"[storage] hydrate failed for {path}: {e}")

    def schedule(self, path: str, data: Any):
        """非同期push: バックグラウンドスレッドが PUSH_INTERVAL 秒以内に送る。
        同じパスへの連続更新は最新だけが送られる（デバウンス）。"""
        if not self._ensure_init():
            return
        with self._lock:
            self._pending[path] = data

    def push_now(self, path: str, data: Any):
        """同期push: 完了まで待機。失敗は黙ってログに出すだけ。"""
        if not self._ensure_init():
            return
        try:
            self._do_push(path, data)
            with self._lock:
                self._pending.pop(path, None)
        except Exception as e:
            print(f"[storage] push_now failed for {path}: {e}")

    def _flush_loop(self):
        while True:
            time.sleep(PUSH_INTERVAL)
            with self._lock:
                to_push = list(self._pending.items())
                self._pending.clear()
            failed: list[tuple[str, Any]] = []
            for path, data in to_push:
                try:
                    self._do_push(path, data)
                except Exception as e:
                    print(f"[storage] flush failed for {path}: {e}")
                    failed.append((path, data))
            if failed:
                with self._lock:
                    for path, data in failed:
                        if path not in self._pending:
                            self._pending[path] = data

    def _do_push(self, path: str, data: Any):
        content_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        body = {
            "message": f"update {path}",
            "content": base64.b64encode(content_bytes).decode("ascii"),
        }
        sha = self._sha_cache.get(path)
        if sha:
            body["sha"] = sha
        r = requests.put(self._url(path), headers=self._headers, json=body, timeout=HTTP_TIMEOUT)
        if r.status_code in (409, 422):
            # SHAミスマッチ。最新SHAを取り直して1回だけリトライ。
            fresh_sha = self._fetch_sha(path)
            if fresh_sha:
                body["sha"] = fresh_sha
            else:
                body.pop("sha", None)
            r = requests.put(self._url(path), headers=self._headers, json=body, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        new = r.json()
        if isinstance(new, dict) and "content" in new and isinstance(new["content"], dict):
            new_sha = new["content"].get("sha")
            if new_sha:
                self._sha_cache[path] = new_sha

    def _fetch_sha(self, path: str) -> str | None:
        try:
            r = requests.get(self._url(path), headers=self._headers, timeout=HTTP_TIMEOUT)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json().get("sha")
        except Exception:
            return None


_store = _GitHubStore()


def hydrate(paths: list[str]):
    _store.hydrate(paths)


def push(path: str, data: Any, sync: bool = False):
    if sync:
        _store.push_now(path, data)
    else:
        _store.schedule(path, data)


def enabled() -> bool:
    return _store.enabled
