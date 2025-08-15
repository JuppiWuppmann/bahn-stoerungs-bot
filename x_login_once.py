from playwright.sync_api import sync_playwright
import os, time

STORAGE = "x_storage.json"
X_USERNAME = os.getenv("X_USERNAME")
X_PASSWORD = os.getenv("X_PASSWORD")

if not X_USERNAME or not X_PASSWORD:
    raise RuntimeError("❌ X_USERNAME / X_PASSWORD fehlen!")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://x.com/login", timeout=60000)
    time.sleep(3)
    page.fill('input[name="text"]', X_USERNAME)
    page.keyboard.press("Enter")
    time.sleep(3)
    page.fill('input[name="password"]', X_PASSWORD)
    page.keyboard.press("Enter")
    time.sleep(8)

    context.storage_state(path=STORAGE)
    print(f"✅ Login gespeichert in {STORAGE}")
    browser.close()
