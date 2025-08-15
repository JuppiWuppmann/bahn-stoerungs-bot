import os
import asyncio
from datetime import datetime
from discord.ext import commands
import discord
from aiohttp import web
from playwright.async_api import async_playwright
from io import BytesIO
import json
import traceback

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = os.getenv("ADMIN_ID")
POST_TO_X = os.getenv("POST_TO_X", "0") == "1"
X_USERNAME = os.getenv("X_USERNAME")
X_PASSWORD = os.getenv("X_PASSWORD")

CLICK_TIMEOUT = 20000
OVERLAY_MAX_WAIT = 25000
PAGE_LOAD_TIMEOUT = 80000

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_stoerungen = {}
last_check_time = None
x_context = None
x_page = None
cookies_file = "x_cookies.json"

# ------------------ X LOGIN ------------------

async def init_x_session():
    global x_context, x_page
    if not POST_TO_X:
        print("🐦 POST_TO_X ist deaktiviert.")
        return None

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    x_context = await browser.new_context()

    # Cookies laden
    if os.path.exists(cookies_file):
        try:
            with open(cookies_file, "r") as f:
                cookies = json.load(f)
            await x_context.add_cookies(cookies)
            x_page = await x_context.new_page()
            await x_page.goto("https://x.com/home", timeout=60000)
            await x_page.wait_for_selector("nav", timeout=15000)
            print("✅ X: Cookies geladen und Session wiederhergestellt.")
            return
        except Exception as e:
            print("⚠️ Cookies ungültig, neuer Login nötig:", e)

    # Login
    print("🔐 X: Starte Login-Prozess...")
    x_page = await x_context.new_page()
    await x_page.goto("https://x.com/login", timeout=60000)
    await x_page.fill("input[name='text']", X_USERNAME)
    await x_page.press("input[name='text']", "Enter")
    await asyncio.sleep(2)
    await x_page.fill("input[name='password']", X_PASSWORD)
    await x_page.press("input[name='password']", "Enter")
    await x_page.wait_for_selector("nav", timeout=20000)
    print("✅ X: Login erfolgreich.")

    # Cookies speichern
    cookies = await x_context.cookies()
    with open(cookies_file, "w") as f:
        json.dump(cookies, f)
    print("💾 X: Cookies gespeichert.")

async def post_to_x(text):
    if not POST_TO_X or not x_page:
        return
    try:
        await x_page.goto("https://x.com/compose/tweet", timeout=60000)
        await x_page.wait_for_selector("div[role='textbox']", timeout=15000)
        await x_page.fill("div[role='textbox']", text)
        await x_page.click("div[data-testid='tweetButton']")
        print(f"🐦 X: Tweet gesendet → {text}")
    except Exception as e:
        print("❌ Fehler beim Posten auf X:", e)

# ------------------ DISCORD ------------------

async def handle_health(request):
    return web.Response(text="OK")

async def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Health-Webserver läuft auf Port {port}")

async def safe_send_to_channel(channel, content=None, file_bytes=None, filename=None):
    try:
        if file_bytes:
            file_bytes.seek(0)
            await channel.send(content=content, file=discord.File(fp=file_bytes, filename=filename))
        else:
            await channel.send(content)
        return True
    except Exception as e:
        print("❌ Fehler beim Senden:", e)
        return False

# ------------------ SCRAPER ------------------

async def scrape_stoerungen():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(viewport={"width": 1366, "height": 900})
        page = await context.new_page()
        await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)
        await page.wait_for_selector("table tbody tr", timeout=20000)
        rows = await page.query_selector_all("table tbody tr")

        stoerungen = []
        for row in rows:
            cols = await row.query_selector_all("td")
            if len(cols) < 8:
                continue
            id_text = (await cols[0].inner_text()).strip()
            typ = (await cols[1].inner_text()).strip()
            ort = (await cols[2].inner_text()).strip()
            region = (await cols[3].inner_text()).strip()
            wirkung = (await cols[4].inner_text()).strip()
            ursache = (await cols[5].inner_text()).strip()
            gueltig_von = (await cols[6].inner_text()).strip()
            gueltig_bis_str = (await cols[7].inner_text()).strip()

            try:
                gueltig_bis_dt = datetime.strptime(gueltig_bis_str, "%d.%m.%Y %H:%M")
            except:
                gueltig_bis_dt = None

            if typ.lower() in ["baustelle", "streckenruhe"]:
                continue

            stoerungen.append({
                "id": id_text,
                "gueltig_bis": gueltig_bis_dt,
                "gueltig_von": gueltig_von,
                "typ": typ,
                "ort": ort,
                "region": region,
                "wirkung": wirkung,
                "ursache": ursache
            })
        await context.close()
        await browser.close()
        return stoerungen

# ------------------ CHECK LOOP ------------------

async def check_stoerungen():
    global last_stoerungen, last_check_time
    channel = bot.get_channel(CHANNEL_ID)

    while not bot.is_closed():
        try:
            stoerungen = await scrape_stoerungen()
            last_check_time = datetime.now()
            current_ids = {s["id"] for s in stoerungen}

            # Beendete Störungen
            for sid, details in list(last_stoerungen.items()):
                if sid not in current_ids or (details["gueltig_bis"] and details["gueltig_bis"] < datetime.now()):
                    msg = f"""✅ **Bahn-Störung behoben!**
🆔 {sid}
📍 {details['ort']}
🚦 {details['wirkung']}
📋 {details['ursache']}
⏰ {details['gueltig_von']} → {details['gueltig_bis'].strftime('%d.%m.%Y %H:%M') if details['gueltig_bis'] else 'unbekannt'}"""
                    if channel:
                        await safe_send_to_channel(channel, msg)
                    await post_to_x(msg)
                    del last_stoerungen[sid]

            # Neue Störungen
            for s in stoerungen:
                if s["id"] not in last_stoerungen:
                    last_stoerungen[s["id"]] = s
                    msg = f"""🚨 Neue Bahn-Störung!
🆔 {s['id']}
📍 {s['ort']}
🚦 {s['wirkung']}
📋 {s['ursache']}"""
                    if channel:
                        await safe_send_to_channel(channel, msg)
                    await post_to_x(msg)

        except Exception:
            traceback.print_exc()

        await asyncio.sleep(600)

# ------------------ BOT EVENTS ------------------

@bot.command()
async def status(ctx):
    if ADMIN_ID and str(ctx.author.id) != str(ADMIN_ID):
        await ctx.send("❌ Nicht berechtigt.")
        return
    if last_check_time:
        await ctx.send(f"✅ Letzte Prüfung: {last_check_time.strftime('%d.%m.%Y %H:%M:%S')}")
    else:
        await ctx.send("⏳ Noch keine Prüfung.")

@bot.event
async def on_ready():
    print(f"🤖 Bot ready as {bot.user}")
    await init_x_session()
    bot.loop.create_task(check_stoerungen())

async def main():
    await asyncio.gather(start_web_server(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot beendet.")

