import os
from pathlib import Path
from playwright.async_api import async_playwright

STORAGE_FILE = "x_storage.json"
POST_TO_X = os.getenv("POST_TO_X", "0") == "1"

async def post_to_x(msg: str):
    if not POST_TO_X:
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(
            storage_state=STORAGE_FILE if Path(STORAGE_FILE).exists() else None
        )
        page = await context.new_page()

        await page.goto("https://x.com/compose/tweet", timeout=60000)
        await page.fill('div[role="textbox"]', msg)
        await page.click('div[data-testid="tweetButton"]')

        await browser.close()
