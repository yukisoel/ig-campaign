import asyncio
import os
import re
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()
IG_USERNAME = os.getenv("IG_USERNAME")
IG_PASSWORD = os.getenv("IG_PASSWORD")
POST_URL = "https://www.instagram.com/p/DWGYT2YD5VS/"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="ja-JP",
        )
        page = await context.new_page()

        # ログイン
        await page.goto("https://www.instagram.com/accounts/login/")
        await asyncio.sleep(3)
        await page.fill('input[name="email"]', IG_USERNAME)
        await page.fill('input[name="pass"]', IG_PASSWORD)
        try:
            btn = page.get_by_role("button", name=re.compile(r"ログイン|Log in", re.IGNORECASE))
            await btn.click(timeout=10000)
        except Exception:
            await page.press('input[name="pass"]', "Enter")
        await asyncio.sleep(5)

        # 投稿ページへ
        await page.goto(POST_URL)
        await asyncio.sleep(4)

        # いいね数テキストを含む要素を探す
        print("=== いいね関連の要素 ===")
        elements = await page.query_selector_all("span, a, button, div[role='button']")
        for el in elements:
            try:
                text = (await el.inner_text()).strip()
                if text and re.search(r'いいね|like|❤|♡|\d+', text, re.IGNORECASE) and len(text) < 50:
                    tag = await page.evaluate("el => el.tagName", el)
                    role = await el.get_attribute("role") or ""
                    href = await el.get_attribute("href") or ""
                    print(f"  [{tag}] role={role!r} href={href!r} text={text!r}")
            except Exception:
                pass

        await asyncio.sleep(3)
        await browser.close()

asyncio.run(main())
