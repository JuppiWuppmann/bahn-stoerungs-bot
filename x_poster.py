# x_poster.py
import os
import asyncio
from playwright.async_api import async_playwright

X_USER = os.getenv("X_USER")
X_PASS = os.getenv("X_PASS")

STORAGE_FILE = "x_storage.json"

async def post_to_x(text: str):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = None

            # Falls gespeicherte Session existiert
            if os.path.exists(STORAGE_FILE):
                context = await browser.new_context(storage_state=STORAGE_FILE)
            else:
                context = await browser.new_context()

            page = await context.new_page()
            await page.goto("https://x.com/login", timeout=60000)

            # Falls nicht eingeloggt
            if "login" in page.url and X_USER and X_PASS:
                await page.fill('input[name="text"]', X_USER)
                await page.keyboard.press("Enter")
                await asyncio.sleep(2)
                await page.fill('input[name="password"]', X_PASS)
                await page.keyboard.press("Enter")
                await asyncio.sleep(5)
                await context.storage_state(path=STORAGE_FILE)

            # Tweet schreiben
            await page.goto("https://x.com/compose/tweet", timeout=60000)
            await page.fill('div[contenteditable="true"]', text[:270])
            await page.click('div[data-testid="tweetButtonInline"]')

            await asyncio.sleep(3)
            await browser.close()
            print("✅ Tweet gesendet:", text[:50])
    except Exception as e:
        print("❌ Fehler beim Tweet:", e)
