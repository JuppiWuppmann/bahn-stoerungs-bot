# bot.py
import os, asyncio, traceback
from datetime import datetime
import discord
from discord.ext import commands
from aiohttp import web
from playwright.async_api import async_playwright
from keepalive import keep_alive

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
bot = commands.Bot(command_prefix="!", intents=intents)

# State
last_stoerungen = {}
last_check_time = None

# Playwright global
_pw = None
_browser = None
_x_context = None

# ---------------- Healthcheck (Render) ----------------
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

# ---------------- Playwright Setup ----------------
async def ensure_playwright_and_browser():
    global _pw, _browser
    if _browser:
        return
    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage",
              "--disable-gpu", "--blink-settings=imagesEnabled=false"]
    )

# ---------------- X: Login + Post ----------------
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
    if not POST_TO_X: return
    if not _x_context: await init_x_context()
    if not _x_context: return
    try:
        page = await _x_context.new_page()
        await page.goto("https://x.com/compose/tweet", timeout=60000)
        tb = await page.wait_for_selector('div[role="textbox"]', timeout=10000)
        chunks = _chunk_for_x(message)
        await tb.click()
        await page.keyboard.type(chunks[0])
        for extra in chunks[1:]:
            try:
                add_btn = await page.wait_for_selector('div[data-testid="addButton"]', timeout=4000)
                await add_btn.click()
            except: pass
            await page.keyboard.type("\n\n" + extra)
        btn = await page.wait_for_selector('div[data-testid="tweetButton"]', timeout=5000)
        await btn.click()
        await page.wait_for_timeout(1200)
        await page.close()
    except Exception as e:
        print("❌ Fehler bei X:", e)
        try:
            await page.screenshot(path="x_error.png")
            print("📸 Screenshot gespeichert: x_error.png")
        except:
            pass
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            await safe_send_to_channel(channel, f"⚠️ X-Post fehlgeschlagen: {e}")

def build_x_text(item):
    return f"ID: {item['id']}\nOrt: {item['ort']}\nWirkung: {item['wirkung']}\nUrsache: {item['ursache']}"

# ---------------- Scraper ----------------
async def scrape_stoerungen():
    await ensure_playwright_and_browser()
    context = await _browser.new_context(viewport={"width": 1280, "height": 800})
    page = await context.new_page()
    stoerungen = []
    try:
        await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)

        # Screenshot und HTML speichern
        await page.screenshot(path="debug_page.png", full_page=True)
        print("📸 Screenshot gespeichert: debug_page.png")
        html = await page.content()
        with open("debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("📄 HTML gespeichert: debug.html")

        # Overlay schließen, wenn vorhanden
        try:
            btn = await page.query_selector("button:has-text('OK')")
            if btn: 
                await btn.click()
                print("ℹ️ Overlay geschlossen")
        except: pass

        # Filter öffnen
        try:
            await page.click("button:has-text('Filter')", timeout=8000)
            print("✅ Filter geöffnet")
        except: print("⚠️ Filter-Button nicht gefunden")

        # Nur „Störungen“ anhaken
        try:
            cb = await page.wait_for_selector("label:has-text('Störungen') input[type='checkbox']", timeout=5000)
            if not await cb.is_checked():
                await cb.click()
                print("✅ Checkbox 'Störungen' angehakt")
        except: print("⚠️ Checkbox 'Störungen' nicht gefunden")

        # „Einschränkungen“ aktivieren
        try:
            await page.click("text=Einschränkungen", timeout=8000)
            print("✅ Tab 'Einschränkungen' aktiviert")
        except: print("⚠️ Tab 'Einschränkungen' nicht gefunden")

        # Statt starrem Wait: Schleife
        rows = []
        for i in range(6):  # bis zu 60s
            rows = await page.query_selector_all("table tbody tr")
            if rows:
                print(f"✅ {len(rows)} Zeilen gefunden")
                break
            print(f"⏳ Noch keine Tabelle, Versuch {i+1}")
            await asyncio.sleep(10)

        if not rows:
            print("❌ Keine Tabelle gefunden – evtl. Struktur anders?")
            return []

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
                if typ.lower() in ("baustelle", "streckenruhe"): continue
                try: gv_dt = datetime.strptime(gueltig_von, "%d.%m.%Y %H:%M")
                except: gv_dt = None
                try: gb_dt = datetime.strptime(gueltig_bis, "%d.%m.%Y %H:%M")
                except: gb_dt = None
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
            except: continue

        stoerungen.sort(key=lambda x: x["gueltig_von"] or datetime.min, reverse=True)

    except Exception as e:
        print("❌ Fehler beim Scraping:", e)
        traceback.print_exc()
    finally:
        await page.close()
        await context.close()
    return stoerungen

# ---------------- Notify-Loop ----------------
async def safe_send_to_channel(channel, content):
    try: await channel.send(content)
    except Exception as e: print("❌ Discord-Sendefehler:", e)

async def check_stoerungen():
    global last_stoerungen, last_check_time
    while not bot.is_closed():
        try:
            stoerungen = await scrape_stoerungen()
            last_check_time = datetime.now()
            current_ids = {s["id"] for s in stoerungen}
            channel = bot.get_channel(CHANNEL_ID)

            for sid, d in list(last_stoerungen.items()):
                ended = sid not in current_ids or (d["gueltig_bis"] and d["gueltig_bis"] < datetime.now())
                if ended:
                    if channel:
                        await safe_send_to_channel(channel, f"✅ Behoben: {sid} in {d['ort']}")
                    await post_to_x_minimal(build_x_text({
                        "id": sid, "ort": d["ort"], "wirkung": d["wirkung"], "ursache": d["ursache"]
                    }))
                    del last_stoerungen[sid]

            for s in stoerungen:
                if s["id"] not in last_stoerungen:
                    last_stoerungen[s["id"]] = s
                    if channel: await safe_send_to_channel(channel, s["discord_text"])
                    await post_to_x_minimal(build_x_text(s))
        except Exception as e:
            print("⚠️ Loop-Fehler:", e)
            traceback.print_exc()
        await asyncio.sleep(600)

# ---------------- Commands ----------------
@bot.command()
async def status(ctx):
    if ADMIN_ID and str(ctx.author.id) != str(ADMIN_ID):
        return await ctx.send("❌ Nicht berechtigt.")
    if last_check_time:
        await ctx.send(f"✅ Letzte Prüfung: {last_check_time.strftime('%d.%m.%Y %H:%M:%S')}")
    else:
        await ctx.send("⏳ Noch keine Prüfung.")

@bot.event
async def on_ready():
    print(f"🤖 Bot ready as {bot.user}")
    if POST_TO_X: await init_x_context()
    bot.loop.create_task(check_stoerungen())

# ---------------- Main ----------------
async def main():
    await asyncio.gather(start_web_server(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("🛑 Bot beendet.")
