import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto("https://www.instagram.com/accounts/login/")
        await asyncio.sleep(5)
        inputs = await page.query_selector_all("input")
        print(f"input要素の数: {len(inputs)}")
        for i, inp in enumerate(inputs):
            attrs = await page.evaluate(
                "el => ({name: el.name, type: el.type, placeholder: el.placeholder, id: el.id})",
                inp
            )
            print(f"  input[{i}]: {attrs}")
        await browser.close()

asyncio.run(test())
