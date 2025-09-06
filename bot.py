import os, json, asyncio, traceback
from datetime import datetime
import discord
from discord.ext import commands
from playwright.async_api import async_playwright
from atproto import Client

# ---------------- Konfiguration ----------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID    = int(os.getenv("CHANNEL_ID", "0"))
BSKY_HANDLE   = os.getenv("BSKY_HANDLE")
BSKY_PASSWORD = os.getenv("BSKY_PASSWORD")
STATE_FILE = "sent.json"
PAGE_LOAD_TIMEOUT = 80000

# ---------------- State ----------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ---------------- Popups entfernen ----------------
async def close_popups(page):
    selectors = [
        "button:has-text('OK')",
        "button:has-text('Schließen')",
        "button[aria-label='Schließen']",
        "button[aria-label='Close']",
        ".close-button",
        ".modal-close"
    ]
    for selector in selectors:
        try:
            btn = await page.query_selector(selector)
            if btn:
                await btn.click()
                print(f"✅ Popup geschlossen: {selector}")
        except Exception:
            continue

# ---------------- Scraper ----------------
async def scrape_stoerungen():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()
        stoerungen = []

        try:
            await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)

            # Consent-Overlay entfernen
            await page.evaluate("document.getElementById('usercentrics-cmp-ui')?.remove()")
            print("🧹 Consent-Overlay entfernt")

            await close_popups(page)

            # Filter öffnen
            try:
                await page.click("button:has-text('Filter')", timeout=8000)
                print("✅ Filtermenü geöffnet")
            except Exception as e:
                print("⚠️ Filtermenü konnte nicht geöffnet werden:", e)

            # Checkboxen gezielt setzen
            checkboxen = {
                "Baustellen": False,
                "Streckenruhe": False,
                "Störungen": True
            }

            for label, should_be_checked in checkboxen.items():
                try:
                    selector = f"label:has-text('{label}') input[type='checkbox']"
                    cb = await page.wait_for_selector(selector, timeout=5000)
                    await cb.scroll_into_view_if_needed()
                    is_checked = await cb.is_checked()
                    if is_checked != should_be_checked:
                        await cb.click()
                        print(f"🔧 Checkbox '{label}' {'aktiviert' if should_be_checked else 'deaktiviert'}")
                    else:
                        print(f"✅ Checkbox '{label}' bereits korrekt gesetzt")
                except Exception as e:
                    print(f"⚠️ Checkbox '{label}' konnte nicht verarbeitet werden:", e)

            # „Einschränkungen“ aktivieren
            try:
                await page.click("text=Einschränkungen", timeout=8000)
                print("✅ Tab 'Einschränkungen' aktiviert")
            except Exception as e:
                print("⚠️ Tab 'Einschränkungen' konnte nicht aktiviert werden:", e)

            # Tabelle laden
            for i in range(6):
                rows = await page.query_selector_all("table tbody tr")
                if rows: break
                await asyncio.sleep(5)

            print(f"🔍 Tabellenzeilen gefunden: {len(rows)}")

            for row in rows:
                try:
                    cols = await row.query_selector_all("td")
                    if len(cols) < 8: continue
                    id_text     = (await cols[0].inner_text()).strip()
                    typ         = (await cols[1].inner_text()).strip()
                    ort         = (await cols[2].inner_text()).strip()
                    region      = (await cols[3].inner_text()).strip()
                    wirkung     = (await cols[4].inner_text()).strip()
                    ursache     = (await cols[5].inner_text()).strip()
                    gueltig_von = (await cols[6].inner_text()).strip()
                    gueltig_bis = (await cols[7].inner_text()).strip()

                    stoerungen.append({
                        "id": id_text,
                        "ort": ort,
                        "region": region,
                        "wirkung": wirkung,
                        "ursache": ursache,
                        "gueltig_von": gueltig_von,
                        "gueltig_bis": gueltig_bis,
                        "discord_text": (
                            f"🚨 **Neue Bahn-Störung!**\n"
                            f"🆔 {id_text}\n📍 {ort}\n🗺️ {region}\n"
                            f"🚦 {wirkung}\n📋 {ursache}\n"
                            f"⏰ {gueltig_von} → {gueltig_bis}"
                        ),
                        "bsky_text": (
                            f"🚨 Neue Bahn-Störung!\n"
                            f"ID: {id_text}\nOrt: {ort}\nRegion: {region}\n"
                            f"Wirkung: {wirkung}\nUrsache: {ursache}\n"
                            f"⏰ {gueltig_von} → {gueltig_bis}"
                        )
                    })
                except Exception:
                    continue

        except Exception as e:
            print("❌ Fehler beim Scraping:", e)
            traceback.print_exc()
        finally:
            await context.close()
            await browser.close()

        return stoerungen

# ---------------- Discord ----------------
async def send_discord(message: str):
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            channel = client.get_channel(CHANNEL_ID)
            if channel:
                await channel.send(message)
        finally:
            await client.close()

    await client.start(DISCORD_TOKEN)

# ---------------- Bluesky ----------------
def send_bluesky(message: str):
    try:
        client = Client()
        client.login(BSKY_HANDLE, BSKY_PASSWORD)
        client.send_post(message)
        print("✅ Bluesky gepostet")
    except Exception as e:
        print("❌ Bluesky-Fehler:", e)

# ---------------- Main ----------------
async def main():
    state = load_state()
    stoerungen = await scrape_stoerungen()

    new_found = False
    for s in stoerungen:
        if s["id"] not in state:
            print(f"👉 Neue Störung: {s['id']}")
            await send_discord(s["discord_text"])
            send_bluesky(s["bsky_text"])
            state[s["id"]] = True
            new_found = True

    if new_found:
        save_state(state)
        print("✅ State gespeichert")
    else:
        print("ℹ️ Keine neuen Störungen")

if __name__ == "__main__":
    asyncio.run(main())
