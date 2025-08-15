# x_login_once.py
from playwright.sync_api import sync_playwright
import time, json, os

STORAGE = "x_storage.json"
X_USERNAME = os.getenv("X_USERNAME")
X_PASSWORD = os.getenv("X_PASSWORD")

def main():
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

        # Wenn wir auf Home sind, speichern wir die Session
        context.storage_state(path=STORAGE)
        print(f"✅ Cookies gespeichert in {STORAGE}")

        browser.close()

if __name__ == "__main__":
    main()
