#!/usr/bin/env python3
"""
Instagram Campaign Scraper
Usage:
  python scraper.py <post_url> --type likes
  python scraper.py <post_url> --type comments
"""
import argparse
import asyncio
import csv
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

load_dotenv()

IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")
OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

IGNORED_PATHS = {
    "explore", "reels", "stories", "accounts", "p", "tv", "reel",
    "liked_by", "audio", "directory", "about", "blog", "help",
    "legal", "press", "api", "contact", "privacy", "safety", "support",
}


def parse_follower_count(text: str) -> int:
    """フォロワー数テキストを整数に変換 (例: '1.2万', '12.3K', '1,234')"""
    text = text.strip().replace(",", "").replace(" ", "")
    text_lower = text.lower()
    try:
        if "万" in text:
            return int(float(text.replace("万", "")) * 10_000)
        if "億" in text:
            return int(float(text.replace("億", "")) * 100_000_000)
        if text_lower.endswith("m"):
            return int(float(text_lower[:-1]) * 1_000_000)
        if text_lower.endswith("k"):
            return int(float(text_lower[:-1]) * 1_000)
        return int(float(text))
    except (ValueError, TypeError):
        return 0


def is_valid_username(href: str) -> bool:
    """有効なInstagramユーザー名パスかチェック"""
    if not href:
        return False
    username = href.strip("/")
    if not username or "/" in username:
        return False
    if username in IGNORED_PATHS:
        return False
    if not re.match(r'^[\w.]+$', username):
        return False
    return True


async def wait_random(min_sec: float = 1.0, max_sec: float = 3.0):
    await asyncio.sleep(random.uniform(min_sec, max_sec))


async def login(page: Page) -> None:
    print("Instagramにログイン中...")
    await page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
    await wait_random(2, 3)

    # Cookie同意ダイアログを閉じる（地域によって表示される）
    for btn_text in ["Allow all cookies", "Accept All", "すべてのCookieを許可", "同意する"]:
        try:
            btn = page.get_by_role("button", name=re.compile(btn_text, re.IGNORECASE))
            await btn.click(timeout=3000)
            await wait_random(1, 2)
            break
        except Exception:
            continue

    try:
        await page.wait_for_selector('input[name="email"]', timeout=20000)
    except PlaywrightTimeout:
        screenshot_path = OUTPUT_DIR / "login_error.png"
        await page.screenshot(path=str(screenshot_path))
        print("エラー: ログインページの読み込みに失敗しました。")
        print(f"  スクリーンショットを保存しました: {screenshot_path}")
        print(f"  現在のURL: {page.url}")
        sys.exit(1)

    await wait_random(1, 2)
    await page.fill('input[name="email"]', IG_USERNAME)
    await page.fill('input[name="pass"]', IG_PASSWORD)
    # 「ログイン」ボタンをクリック（テキストまたはroleで特定）
    try:
        btn = page.get_by_role("button", name=re.compile(r"ログイン|Log in|Log In", re.IGNORECASE))
        await btn.click(timeout=10000)
    except Exception:
        # フォールバック: パスワード欄でEnterキーを押す
        await page.press('input[name="pass"]', "Enter")

    try:
        await page.wait_for_url(re.compile(r"instagram\.com(?!/accounts/login)"), timeout=30000)
    except PlaywrightTimeout:
        print("エラー: ログインに失敗しました。.envのIG_USERNAME / IG_PASSWORDを確認してください。")
        sys.exit(1)

    await wait_random(2, 3)

    # 「ログイン情報を保存」「通知を許可」などのダイアログを閉じる
    for _ in range(3):
        try:
            btn = page.get_by_role("button", name=re.compile(r"後で|Not Now|後でする|Skip", re.IGNORECASE))
            await btn.click(timeout=3000)
            await wait_random(1, 2)
        except Exception:
            break

    print("ログイン完了\n")


async def collect_users_from_modal(page: Page) -> set[str]:
    """いいねモーダル内のユーザー名を全件スクロールして収集"""
    usernames: set[str] = set()

    try:
        await page.wait_for_selector('[role="dialog"]', timeout=10000)
    except PlaywrightTimeout:
        print("エラー: いいねモーダルが開きませんでした。")
        return usernames

    stale_rounds = 0
    prev_count = -1

    while stale_rounds < 10:
        links = await page.query_selector_all('[role="dialog"] a[href^="/"]')
        for link in links:
            href = await link.get_attribute("href")
            if href and is_valid_username(href):
                usernames.add(href.strip("/"))

        current_count = len(usernames)
        print(f"  収集中: {current_count} 人...", end="\r")

        stale_rounds = 0 if current_count != prev_count else stale_rounds + 1
        prev_count = current_count

        # モーダル内を少しずつスクロール（遅延ロードに対応）
        await page.evaluate("""() => {
            const dialog = document.querySelector('[role="dialog"]');
            if (!dialog) return;
            const scrollable = Array.from(dialog.querySelectorAll('*')).find(
                el => el.scrollHeight > el.clientHeight + 10 && el.clientHeight > 100
            ) || dialog;
            scrollable.scrollTop += 800;
        }""")
        await wait_random(2.0, 3.0)

    print()  # 改行
    return usernames


async def get_likes_users(page: Page, post_url: str) -> list[str]:
    print("対象投稿に移動中...")
    await page.goto(post_url, wait_until="domcontentloaded")
    await wait_random(3, 4)

    # いいね数ボタン（span[role="button"] で数字のみのテキスト）をクリック
    clicked = False
    try:
        # いいね数は section 内の span[role="button"] で最初の数値テキスト
        spans = await page.query_selector_all('span[role="button"]')
        for span in spans:
            text = (await span.inner_text()).strip().replace(",", "")
            if text.isdigit() and int(text) > 0:
                await span.click()
                clicked = True
                await wait_random(1, 2)
                break
    except Exception:
        pass

    if not clicked:
        await page.screenshot(path=str(OUTPUT_DIR / "likes_button_error.png"))
        print("エラー: いいねリストを開けませんでした。")
        print(f"  スクリーンショット: output/likes_button_error.png")
        return []

    usernames = await collect_users_from_modal(page)
    return list(usernames)


async def get_comment_users(page: Page, post_url: str) -> list[str]:
    print("対象投稿に移動中...")
    await page.goto(post_url, wait_until="domcontentloaded")
    await wait_random(3, 4)

    usernames: set[str] = set()

    # 「コメントをもっと見る」を繰り返しクリックして全件ロード
    load_count = 0
    while True:
        try:
            more_btn = page.get_by_role("button", name=re.compile(
                r"コメントをもっと見る|Load more comments|View all \d+ comments",
                re.IGNORECASE
            ))
            await more_btn.click(timeout=3000)
            load_count += 1
            print(f"  コメント追加読み込み: {load_count} 回目...", end="\r")
            await wait_random(1.5, 2.5)
        except Exception:
            break

    if load_count > 0:
        print()

    # デバッグ: 投稿ページのスクリーンショットを保存
    await page.screenshot(path=str(OUTPUT_DIR / "post_page.png"))

    # コメント投稿者のプロフィールリンクを収集（複数セレクタを試行）
    for selector in ['article a[href^="/"]', 'a[href^="/"]']:
        links = await page.query_selector_all(selector)
        for link in links:
            href = await link.get_attribute("href")
            if href and is_valid_username(href):
                usernames.add(href.strip("/"))
        if usernames:
            break

    print(f"  取得したリンク数: {len(usernames)}")
    return list(usernames)


async def get_follower_count(page: Page, username: str) -> int:
    """プロフィールページからフォロワー数を取得"""
    try:
        await page.goto(
            f"https://www.instagram.com/{username}/",
            wait_until="domcontentloaded",
            timeout=15000,
        )
        await wait_random(1.5, 2.5)

        # 方法1: <meta name="description"> から "X followers" をパース
        meta = await page.query_selector('meta[name="description"]')
        if meta:
            content = await meta.get_attribute("content") or ""
            match = re.search(r'([\d,\.]+[KkMm万億]?)\s*[Ff]ollowers', content)
            if match:
                return parse_follower_count(match.group(1))

        # 方法2: フォロワーリンク内の span[title] または span テキスト
        for selector in ['a[href*="/followers/"] span[title]', 'a[href*="/followers/"] span']:
            try:
                els = await page.query_selector_all(selector)
                for el in els:
                    for attr in [await el.get_attribute("title"), await el.inner_text()]:
                        if attr and re.match(r'^[\d,\.]+[KkMm万億]?$', attr.strip()):
                            count = parse_follower_count(attr)
                            if count > 0:
                                return count
            except Exception:
                continue

        # 方法3: ページ内JSONデータ
        html = await page.content()
        match = re.search(r'"edge_followed_by":\{"count":(\d+)\}', html)
        if match:
            return int(match.group(1))

        return 0

    except Exception as e:
        print(f"  @{username}: 取得失敗 ({type(e).__name__})")
        return 0


async def main():
    parser = argparse.ArgumentParser(
        description="Instagramキャンペーン参加者リスト作成ツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  python scraper.py https://www.instagram.com/p/XXXXX/ --type likes
  python scraper.py https://www.instagram.com/p/XXXXX/ --type comments
        """,
    )
    parser.add_argument("url", help="Instagram投稿のURL")
    parser.add_argument(
        "--type",
        choices=["likes", "comments"],
        required=True,
        help="キャンペーン参加方法 (likes: いいね / comments: コメント)",
    )
    args = parser.parse_args()

    if not IG_USERNAME or not IG_PASSWORD:
        print("エラー: .envファイルにIG_USERNAMEとIG_PASSWORDを設定してください。")
        print("  cp .env.example .env  して編集してください。")
        sys.exit(1)

    print("=" * 50)
    print("Instagram Campaign Scraper")
    print("=" * 50)
    print(f"対象URL  : {args.url}")
    print(f"参加方法 : {'いいね' if args.type == 'likes' else 'コメント'}")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)  # ブラウザを表示して動作確認
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )
        page = await context.new_page()

        try:
            await login(page)

            if args.type == "likes":
                usernames = await get_likes_users(page, args.url)
            else:
                usernames = await get_comment_users(page, args.url)

            if not usernames:
                print("参加者が見つかりませんでした。処理を終了します。")
                return

            print(f"参加者 {len(usernames)} 人を検出。フォロワー数を取得中...")
            print("（数百人の場合、数分かかります）\n")

            results = []
            for i, username in enumerate(usernames, 1):
                print(f"[{i:3d}/{len(usernames)}] @{username}")
                count = await get_follower_count(page, username)
                results.append({
                    "ユーザー名": username,
                    "フォロワー数": count,
                    "プロフィールURL": f"https://www.instagram.com/{username}/",
                })
                await wait_random(2.0, 4.0)

            # フォロワー数降順ソート
            results.sort(key=lambda x: x["フォロワー数"], reverse=True)

            # CSV出力（Excel対応UTF-8 BOM付き）
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = OUTPUT_DIR / f"campaign_{args.type}_{timestamp}.csv"

            with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=["ユーザー名", "フォロワー数", "プロフィールURL"])
                writer.writeheader()
                writer.writerows(results)

            print(f"\n{'=' * 50}")
            print("完了！")
            print(f"参加者数     : {len(results)} 人")
            print(f"出力ファイル : {output_path}")
            print(f"{'=' * 50}")

        except KeyboardInterrupt:
            print("\n\n処理を中断しました。")
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
