# x_poster.py
import os, asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright

STORAGE_FILE = "x_storage.json"
POST_TO_X = os.getenv("POST_TO_X", "0") == "1"
X_USERNAME = os.getenv("X_USERNAME")
X_PASSWORD = os.getenv("X_PASSWORD")

# Kürzt zu lange Texte für X (280 Zeichen), bricht sauber nach Zeilen/Leerzeichen
def chunk_for_x(text, limit=280):
    parts = []
    cur = ""
    for line in text.splitlines():
        for word in (line.strip() + " ").split(" "):
            add = (word + " ").strip()
            if len(cur) + len(add) + 1 > limit:
                if cur:
                    parts.append(cur.strip())
                cur = add
            else:
                cur += add + " "
        cur = cur.strip() + "\n"
    cur = cur.strip()
    if cur:
        # evtl. noch splitten, falls trotzdem zu lang
        while len(cur) > limit:
            parts.append(cur[:limit])
            cur = cur[limit:]
        if cur:
            parts.append(cur)
    return [p.strip() for p in parts if p.strip()]

async def _ensure_logged_in(context):
    page = await context.new_page()
    await page.goto("https://x.com/home", timeout=60000)
    # Wenn Loginseite kommt, einloggen (nur falls kein STORAGE benutzt wurde)
    if "login" in page.url and X_USERNAME and X_PASSWORD:
        await page.goto("https://x.com/login", timeout=60000)
        await page.fill('input[name="text"]', X_USERNAME)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2000)
        await page.fill('input[name="password"]', X_PASSWORD)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(6000)
        await context.storage_state(path=STORAGE_FILE)
    await page.close()

async def post_to_x(message: str):
    if not POST_TO_X:
        return False, "Posting zu X ist deaktiviert (POST_TO_X != 1)."

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        context = None
        try:
            if Path(STORAGE_FILE).exists():
                context = await browser.new_context(storage_state=STORAGE_FILE)
            else:
                context = await browser.new_context()
                await _ensure_logged_in(context)

            page = await context.new_page()
            await page.goto("https://x.com/compose/tweet", timeout=60000)

            # Tweet-Textbox suchen (mehrere Fallbacks)
            selectors = [
                'div[aria-label="Posten"]',
                'div[aria-label="Tweet text"]',
                'div[data-testid="tweetTextarea_0"]',
            ]
            textbox = None
            for sel in selectors:
                try:
                    textbox = await page.wait_for_selector(sel, timeout=8000)
                    if textbox:
                        break
                except:
                    pass
            if not textbox:
                return False, "Tweet-Textbox nicht gefunden (X hat evtl. UI geändert)."

            # Text in passenden Chunks posten (Threads, falls zu lang)
            chunks = chunk_for_x(message, limit=280)
            # Ersten Chunk
            await textbox.click()
            await page.keyboard.type(chunks[0])
            # Weitere Chunks als Thread hinzufügen
            for extra in chunks[1:]:
                # Plus-Button für Thread
                try:
                    add_btn = await page.wait_for_selector('div[data-testid="toolBar"] div[data-testid="addButton"]', timeout=4000)
                    await add_btn.click()
                except:
                    # Notfalls direkt weiter in die (gleiche) Box tippen
                    pass
                await page.keyboard.type("\n\n" + extra)

            # Tweet absetzen (zwei mögliche Buttons)
            for btn_sel in ['div[data-testid="tweetButton"]', 'div[data-testid="tweetButtonInline"]']:
                try:
                    btn = await page.wait_for_selector(btn_sel, timeout=5000)
                    await btn.click()
                    break
                except:
                    continue

            await page.wait_for_timeout(3000)
            return True, "Gepostet."
        finally:
            if context:
                await context.close()
            await browser.close()
