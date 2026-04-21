#!/usr/bin/env python3
"""
Instagram Graph API いいね全件取得スクリプト
Usage:
  python scraper_graph_api.py <post_url>
"""
import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

BASE_URL = "https://graph.instagram.com/v21.0"


def extract_shortcode(url: str) -> str:
    match = re.search(r'/(?:p|reel)/([A-Za-z0-9_-]+)', url)
    if not match:
        print(f"エラー: URLからショートコードを取得できませんでした: {url}")
        sys.exit(1)
    return match.group(1)


def get_media_id(shortcode: str) -> str:
    """ショートコードからMedia IDを取得"""
    # 自分のメディア一覧からショートコードが一致するものを探す
    url = f"{BASE_URL}/me/media"
    params = {
        "fields": "id,shortcode,like_count",
        "limit": 100,
        "access_token": ACCESS_TOKEN,
    }
    while url:
        resp = requests.get(url, params=params)
        data = resp.json()
        if "error" in data:
            print(f"エラー: {data['error']['message']}")
            sys.exit(1)
        for media in data.get("data", []):
            if media.get("shortcode") == shortcode:
                return media["id"], media.get("like_count", 0)
        # 次ページ
        url = data.get("paging", {}).get("next")
        params = {}
    print(f"エラー: 投稿が見つかりませんでした (shortcode={shortcode})")
    print("  自分のアカウントの投稿か確認してください。")
    sys.exit(1)


def get_likers(media_id: str) -> list[dict]:
    """いいねしたユーザー一覧を全件取得"""
    results = []
    url = f"{BASE_URL}/{media_id}/likes"
    params = {
        "fields": "id,username,followers_count",
        "limit": 100,
        "access_token": ACCESS_TOKEN,
    }
    page = 1
    while url:
        resp = requests.get(url, params=params)
        data = resp.json()
        if "error" in data:
            print(f"\nエラー: {data['error']['message']}")
            print(f"  {len(results)}件まで取得済みです。")
            break
        for user in data.get("data", []):
            username = user.get("username", "")
            followers = user.get("followers_count", 0)
            results.append({
                "ユーザー名": username,
                "フォロワー数": followers,
                "プロフィールURL": f"https://www.instagram.com/{username}/",
            })
            print(f"  [{len(results):4d}] @{username}  フォロワー: {followers:,}", end="\r")
        # 次ページ
        url = data.get("paging", {}).get("next")
        params = {}
        page += 1

    print()
    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python scraper_graph_api.py <post_url>")
        sys.exit(1)

    if not ACCESS_TOKEN:
        print("エラー: .envファイルに IG_ACCESS_TOKEN を設定してください。")
        print("  取得方法: docs/graph_api_setup.md を参照")
        sys.exit(1)

    post_url = sys.argv[1]
    shortcode = extract_shortcode(post_url)

    print("=" * 50)
    print("Instagram Graph API いいね全件取得")
    print("=" * 50)
    print(f"対象URL: {post_url}\n")

    print("投稿を検索中...")
    media_id, like_count = get_media_id(shortcode)
    print(f"投稿取得完了（いいね数: {like_count}件、Media ID: {media_id}）\n")

    print("いいねしたアカウントを収集中...")
    results = get_likers(media_id)

    if not results:
        print("参加者が見つかりませんでした。")
        return

    # フォロワー数降順ソート
    results.sort(key=lambda x: x["フォロワー数"], reverse=True)

    # CSV出力
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"campaign_likes_{timestamp}.csv"
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["ユーザー名", "フォロワー数", "プロフィールURL"])
        writer.writeheader()
        writer.writerows(results)

    print(f"\n{'=' * 50}")
    print("完了！")
    print(f"取得件数     : {len(results)} 人 / {like_count} 件")
    print(f"出力ファイル : {output_path}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
