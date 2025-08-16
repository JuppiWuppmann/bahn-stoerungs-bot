import asyncio
from playwright.async_api import async_playwright

async def post_to_x(message: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # gespeicherter Login aus x_storage.json wird genutzt
        context = await browser.new_context(storage_state="x_storage.json")
        page = await context.new_page()
        await page.goto("https://x.com/compose/tweet")
        await page.fill("div[role='textbox']", message)
        await page.keyboard.press("Control+Enter")  # Tweet absenden
        await asyncio.sleep(3)
        await context.close()
        await browser.close()
