import os, asyncio, traceback
from datetime import datetime
import discord
from discord.ext import commands
from aiohttp import web
from playwright.async_api import async_playwright

# ---------- ENV ----------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID    = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID      = os.getenv("ADMIN_ID")
POST_TO_X     = os.getenv("POST_TO_X", "0") == "1"
X_USERNAME    = os.getenv("X_USERNAME")
X_PASSWORD    = os.getenv("X_PASSWORD")
X_STORAGE     = os.getenv("X_STORAGE", "x_storage.json")

PAGE_LOAD_TIMEOUT = 80000
CLICK_TIMEOUT     = 20000

intents = discord.Intents.default()
intents.message_content = True

# ---------- Bot-Klasse mit setup_hook ----------
class StoerungsBot(commands.Bot):
    async def setup_hook(self):
        print("🚀 setup_hook() wurde aufgerufen")
        if POST_TO_X:
            print("🔧 Initialisiere X-Session...")
            await init_x_context()
        self.loop.create_task(check_stoerungen())

bot = StoerungsBot(command_prefix="!", intents=intents)

# ---------- State ----------
last_stoerungen = {}
last_check_time = None

_pw = None
_browser = None
_x_context = None

# ---------- Healthcheck ----------
async def handle_health(_):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Health-Webserver läuft auf Port {port}")

# ---------- Playwright ----------
async def ensure_playwright_and_browser():
    global _pw, _browser
    if _browser:
        return
    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--blink-settings=imagesEnabled=false"]
    )
    print("✅ Browser gestartet")

# ---------- X: Login + Post ----------
async def init_x_context():
    global _x_context
    if not POST_TO_X:
        return
    await ensure_playwright_and_browser()
    try:
        if os.path.exists(X_STORAGE):
            _x_context = await _browser.new_context(storage_state=X_STORAGE)
        else:
            _x_context = await _browser.new_context()
            page = await _x_context.new_page()
            await page.goto("https://x.com/login", timeout=60000)
            await page.fill('input[name="username"]', X_USERNAME)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1200)
            await page.fill('input[name="password"]', X_PASSWORD)
            await page.keyboard.press("Enter")
            await page.wait_for_selector("nav", timeout=20000)
            await _x_context.storage_state(path=X_STORAGE)
            await page.close()
        print("✅ X: Session bereit")
    except Exception as e:
        print("⚠️ X-Login fehlgeschlagen:", e)

def _chunk_for_x(text, limit=280):
    parts, cur = [], ""
    for token in text.split():
        if len(cur) + 1 + len(token) > limit:
            parts.append(cur.strip()); cur = token
        else:
            cur = (cur + " " + token).strip()
    if cur: parts.append(cur)
    return parts

async def post_to_x_minimal(message: str):
    if not POST_TO_X:
        return
    if not _x_context:
        await init_x_context()
    if not _x_context:
        return
    try:
        chunks = _chunk_for_x(message)
        page = await _x_context.new_page()
        await page.goto("https://x.com/compose/tweet", timeout=60000)
        tb = await page.wait_for_selector('div[role="textbox"]', timeout=10000)
        await tb.click()
        await page.keyboard.type(chunks[0])
        btn = await page.wait_for_selector('div[data-testid="tweetButton"]', timeout=5000)
        await btn.click()
        await page.wait_for_timeout(3000)
        await page.wait_for_selector("article a[href*='/status/']", timeout=10000)
        tweet_link = await page.get_attribute("article a[href*='/status/']", "href")
        first_tweet = "https://x.com" + tweet_link
        await page.close()

        reply_url = first_tweet
        for extra in chunks[1:]:
            page = await _x_context.new_page()
            await page.goto(reply_url, timeout=60000)
            tb = await page.wait_for_selector('div[role="textbox"]', timeout=10000)
            await tb.click()
            await page.keyboard.type(extra)
            btn = await page.wait_for_selector('div[data-testid="tweetButton"]', timeout=5000)
            await btn.click()
            await page.wait_for_timeout(3000)
            await page.wait_for_selector("article a[href*='/status/']", timeout=10000)
            reply_link = await page.get_attribute("article a[href*='/status/']", "href")
            reply_url = "https://x.com" + reply_link
            await page.close()

        print(f"✅ Thread mit {len(chunks)} Tweets gepostet")
    except Exception as e:
        print("❌ Fehler bei X:", e)

def build_x_text(item):
    return f"ID: {item['id']}\nOrt: {item['ort']}\nWirkung: {item['wirkung']}\nUrsache: {item['ursache']}"

# ---------- Scraper ----------
async def scrape_stoerungen():
    await ensure_playwright_and_browser()
    context = await _browser.new_context(viewport={"width": 1280, "height": 800})
    page = await context.new_page()
    stoerungen = []
    try:
        print("🌐 Rufe strecken-info.de auf...")
        await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)

        # Warten auf sichtbare Seite
        try:
            await page.wait_for_selector("button:has-text('Filter')", timeout=10000)
            print("✅ Seite vollständig geladen")
        except:
            print("⚠️ Filter-Button nicht gefunden – Seite evtl. nicht geladen")

        # Overlay oder Cookie-Banner schließen
        for text in ["OK", "Verstanden", "Schließen"]:
            try:
                btn = await page.query_selector(f"button:has-text('{text}')")
                if btn:
                    await btn.click()
                    print(f"✅ Overlay mit '{text}' geschlossen")
                    break
            except:
                pass

        # Filter öffnen
        try:
            await page.click("button:has-text('Filter')", timeout=8000)
            print("✅ Filter geöffnet")
        except:
            print("⚠️ Filter konnte nicht geöffnet werden")

        # Checkbox „Störungen“ aktivieren
        try:
            cb = await page.wait_for_selector("label:has-text('Störungen') input[type='checkbox']", timeout=5000)
            if not await cb.is_checked():
                await cb.click()
                print("✅ Checkbox 'Störungen' aktiviert")
        except:
            print("⚠️ Checkbox 'Störungen' nicht gefunden")

        # Einschränkungen aktivieren
        try:
            await page.click("text=Einschränkungen", timeout=8000)
            print("✅ Einschränkungen aktiviert")
        except:
            print("⚠️ Einschränkungen nicht klickbar")

        # Tabelle laden
        rows = []
        for i in range(6):
            rows = await page.query_selector_all("table tbody tr")
            if rows:
                print(f"📊 Tabelle geladen mit {len(rows)} Zeilen")
                break
            print("⏳ Warte auf Tabelle...")
            await asyncio.sleep(5)

        if not rows:
            print("⚠️ Keine Tabellenzeilen gefunden – Seite evtl. leer oder blockiert")

        # Daten extrahieren
        for row in rows:
            try:
                cols = await row.query_selector_all("td")
                if len(cols) < 8:
                    continue
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
                try:
                    gv_dt = datetime.strptime(gueltig_von, "%d.%m.%Y %H:%M")
                except:
                    gv_dt = None
                try:
                    gb_dt = datetime.strptime(gueltig_bis, "%d.%m.%Y %H:%M")
                except:
                    gb_dt = None
                stoerungen.append({
                    "id": id_text, "typ": typ, "ort": ort, "region": region,
                    "wirkung": wirkung, "ursache": ursache,
                    "gueltig_von": gv_dt, "gueltig_bis": gb_dt,
                    "discord_text": (
                        f"🚨 **Neue Bahn-Störung!**\n"
                        f"🆔 {id_text}\n📍 {ort}\n🗺️ {region}\n"
                        f"🚦 {wirkung}\n📋 {ursache}\n"
                        f"⏰ {gueltig_von} → {gueltig_bis}"
                    )
                })
            except:
                continue

        stoerungen.sort(key=lambda x: x["gueltig_von"] or datetime.min, reverse=True)

    except Exception as e:
        print("❌ Fehler beim Scraping:", e)
        traceback.print_exc()
    finally:
        await page.close()
        await context.close()
    return stoerungen

# ---------- Notify-Loop ----------
async def safe_send_to_channel(channel, content):
    try:
        await channel.send(content)
    except Exception as e:
        print("❌ Discord-Sendefehler:", e)

async def check_stoerungen():
    global last_stoerungen, last_check_time
    while True:
        try:
            print("🔍 Starte Scraping...")
            stoerungen = await scrape_stoerungen()
            print(f"📊 {len(stoerungen)} Störungen gefunden")
            last_check_time = datetime.now()
            current_ids = {s["id"] for s in stoerungen}
            channel = bot.get_channel(CHANNEL_ID)
            print(f"🧪 Channel: {channel}")

            # Behobene Störungen
            for sid, d in list(last_stoerungen.items()):
                ended = sid not in current_ids or (d["gueltig_bis"] and d["gueltig_bis"] < datetime.now())
                if ended:
                    print(f"✅ Behoben: {sid}")
                    if channel:
                        await safe_send_to_channel(channel, f"✅ Behoben: {sid} in {d['ort']}")
                    await post_to_x_minimal(f"✅ Behoben\n{build_x_text(d)}")
                    del last_stoerungen[sid]

            # Neue Störungen
            for s in stoerungen:
                if s["id"] not in last_stoerungen:
                    print(f"🚨 Neue Störung: {s['id']}")
                    last_stoerungen[s["id"]] = s
                    if channel:
                        await safe_send_to_channel(channel, s["discord_text"])
                    await post_to_x_minimal(build_x_text(s))

        except Exception as e:
            print("⚠️ Fehler im Notify-Loop:", e)
            traceback.print_exc()

        print("⏳ Warte 10 Minuten...")
        await asyncio.sleep(600)

# ---------- Discord Commands ----------
@bot.command()
async def status(ctx):
    if ADMIN_ID and str(ctx.author.id) != str(ADMIN_ID):
        return await ctx.send("❌ Nicht berechtigt.")
    if last_check_time:
        await ctx.send(f"✅ Letzte Prüfung: {last_check_time.strftime('%d.%m.%Y %H:%M:%S')}")
    else:
        await ctx.send("⏳ Noch keine Prüfung durchgeführt.")

# ---------- Main ----------
async def main():
    await start_web_server()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot wurde manuell beendet.")
