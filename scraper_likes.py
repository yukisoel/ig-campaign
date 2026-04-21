#!/usr/bin/env python3
"""
Instagram いいね全件取得スクリプト（Instaloader使用）
Usage:
  python scraper_likes.py <post_url>
"""
import csv
import os
import re
import sys
import time
import random
from datetime import datetime
from pathlib import Path

import instaloader
from dotenv import load_dotenv

load_dotenv()

IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)


def extract_shortcode(url: str) -> str:
    match = re.search(r'/p/([A-Za-z0-9_-]+)', url)
    if not match:
        print(f"エラー: URLからショートコードを取得できませんでした: {url}")
        sys.exit(1)
    return match.group(1)


def main():
    if len(sys.argv) < 2:
        print("Usage: python scraper_likes.py <post_url>")
        sys.exit(1)

    post_url = sys.argv[1]
    shortcode = extract_shortcode(post_url)

    if not IG_USERNAME or not IG_PASSWORD:
        print("エラー: .envファイルにIG_USERNAMEとIG_PASSWORDを設定してください。")
        sys.exit(1)

    print("=" * 50)
    print("Instagram いいね全件取得ツール（Instaloader）")
    print("=" * 50)
    print(f"対象URL      : {post_url}")
    print(f"ショートコード: {shortcode}")
    print()

    L = instaloader.Instaloader(
        quiet=True,
        download_pictures=False,
        download_videos=False,
        download_comments=False,
        save_metadata=False,
    )

    print("Instagramにログイン中...")
    try:
        L.login(IG_USERNAME, IG_PASSWORD)
        print("ログイン完了\n")
    except instaloader.exceptions.BadCredentialsException:
        print("エラー: ユーザー名またはパスワードが間違っています。")
        sys.exit(1)
    except instaloader.exceptions.TwoFactorAuthRequiredException:
        print("エラー: 二段階認証が有効です。一時的に無効にして再実行してください。")
        sys.exit(1)
    except Exception as e:
        print(f"エラー: ログインに失敗しました。({e})")
        sys.exit(1)

    print("投稿情報を取得中...")
    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
        print(f"投稿取得完了（いいね数: {post.likes}件）\n")
    except Exception as e:
        print(f"エラー: 投稿の取得に失敗しました。({e})")
        sys.exit(1)

    print("いいねしたアカウントを収集中...")
    print("（レート制限のため時間がかかります）\n")

    results = []
    error_count = 0

    try:
        for i, profile in enumerate(post.get_likes(), 1):
            username = profile.username
            followers = profile.followers
            print(f"[{i:4d}] @{username}  フォロワー: {followers:,}")
            results.append({
                "ユーザー名": username,
                "フォロワー数": followers,
                "プロフィールURL": f"https://www.instagram.com/{username}/",
            })
            # レート制限対策: ランダム待機
            time.sleep(random.uniform(0.5, 1.5))

    except instaloader.exceptions.QueryReturnedNotFoundException:
        print("\nエラー: このアカウントにはいいね一覧を閲覧する権限がありません。")
        print("  投稿の管理者アカウントでログインしているか確認してください。")
    except instaloader.exceptions.TooManyRequestsException:
        print(f"\n警告: レート制限に達しました。{len(results)}件まで取得しました。")
        print("  しばらく時間をおいて再実行してください。")
        error_count += 1
    except Exception as e:
        print(f"\n警告: 途中でエラーが発生しました。({e})")
        print(f"  {len(results)}件まで取得済みです。")
        error_count += 1

    if not results:
        print("参加者が見つかりませんでした。処理を終了します。")
        return

    # フォロワー数降順ソート
    results.sort(key=lambda x: x["フォロワー数"], reverse=True)

    # CSV出力（Excel対応UTF-8 BOM付き）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"campaign_likes_{timestamp}.csv"

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["ユーザー名", "フォロワー数", "プロフィールURL"])
        writer.writeheader()
        writer.writerows(results)

    print(f"\n{'=' * 50}")
    print("完了！" + ("（途中で停止）" if error_count else ""))
    print(f"取得件数     : {len(results)} 人 / {post.likes} 件")
    print(f"出力ファイル : {output_path}")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
