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

# ---------------- Scraper ----------------
async def scrape_stoerungen():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()
        stoerungen = []

        try:
            await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)

            # Overlays entfernen
            await page.evaluate("""
                document.getElementById('usercentrics-cmp-ui')?.remove();
                document.querySelector('.freiefahrt-yvnngg')?.remove();
            """)

            # Filter öffnen
            try:
                await page.click("button:has-text('Filter')", timeout=8000, force=True)
            except: pass

            # Nur "Störungen" anhaken
            try:
                selector = "label:has-text('Störungen') input[type='checkbox']"
                cb = await page.wait_for_selector(selector, timeout=5000)
                if not await cb.is_checked():
                    await cb.click(force=True)
            except: pass

            # Tab "Einschränkungen"
            try:
                await page.click("text=Einschränkungen", timeout=8000, force=True)
            except: pass

            # Tabelle laden
            rows = []
            for i in range(6):
                rows = await page.query_selector_all("table tbody tr")
                if rows: break
                await asyncio.sleep(5)

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

                    if typ.lower() in ("baustelle", "streckenruhe"):
                        continue

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
                except: 
                    continue

        except Exception as e:
            print("❌ Fehler beim Scraping:", e)
            traceback.print_exc()
        finally:
            await context.close()
            await browser.close()

        return stoerungen

# ---------------- Discord ----------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

async def send_discord(message: str):
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
            print("✅ Discord gepostet")
        except Exception as e:
            print("❌ Discord-Fehler:", e)

# ---------------- Bluesky ----------------
def split_message(text, limit=300):
    parts, cur = [], ""
    for word in text.split():
        if len(cur) + len(word) + 1 > limit:
            parts.append(cur.strip())
            cur = word
        else:
            cur += " " + word
    if cur.strip():
        parts.append(cur.strip())
    return parts

def send_bluesky(message: str):
    try:
        client = Client()
        client.login(BSKY_HANDLE, BSKY_PASSWORD)

        parts = split_message(message, 300)
        reply_ref = None

        for part in parts:
            post = client.send_post(part, reply_to=reply_ref)
            reply_ref = post
        print(f"✅ Bluesky: {len(parts)} Teile gepostet")
    except Exception as e:
        print("❌ Bluesky-Fehler:", e)

# ---------------- Main ----------------
async def check_and_post():
    state = load_state()
    stoerungen = await scrape_stoerungen()

    new_found = False
    for s in stoerungen:
        if s["id"] not in state:
            print(f"👉 Neue Störung: {s['id']} ({s['ort']})")

            await send_discord(s["discord_text"])
            send_bluesky(s["bsky_text"])

            state[s["id"]] = True
            new_found = True

    if new_found:
        save_state(state)
        print("✅ State gespeichert")
    else:
        print("ℹ️ Keine neuen Störungen")

@bot.event
async def on_ready():
    print(f"🤖 Bot eingeloggt als {bot.user}")
    await check_and_post()
    await bot.close()  # wichtig, sonst hängt GitHub Action ewig

# ---------------- Start ----------------
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
