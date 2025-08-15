import os, asyncio, traceback
from datetime import datetime
from discord.ext import commands
import discord
from aiohttp import web
from playwright.async_api import async_playwright
from x_poster import post_to_x

# 🔧 Konfiguration
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
POST_TO_X = os.getenv("POST_TO_X", "0") == "1"
PAGE_LOAD_TIMEOUT = 80000

# 🔧 Discord-Setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 🔧 Statusvariablen
last_stoerungen = {}
last_check_time = None

# 🌐 Webserver für Health-Check
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

# 📤 Sicheres Senden an Discord
async def safe_send(channel, text):
    try:
        await channel.send(text)
    except Exception as e:
        print("❌ Discord-Fehler:", e)

# 🔍 Scraping der Störungen
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
            wirkung = (await cols[4].inner_text()).strip()
            ursache = (await cols[5].inner_text()).strip()
            gueltig_bis_str = (await cols[7].inner_text()).strip()
            try:
                gueltig_bis = datetime.strptime(gueltig_bis_str, "%d.%m.%Y %H:%M")
            except:
                gueltig_bis = None

            if typ.lower() in ["baustelle", "streckenruhe"]:
                continue

            stoerungen.append({
                "id": id_text,
                "ort": ort,
                "wirkung": wirkung,
                "ursache": ursache,
                "gueltig_bis": gueltig_bis
            })

        await browser.close()
        return stoerungen

# 🔁 Hauptloop zur Prüfung
async def check_loop():
    global last_stoerungen, last_check_time
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    print(f"📡 Channel gefunden: {channel}")

    while not bot.is_closed():
        try:
            print("🔄 Starte Scraping...")
            stoerungen = await scrape_stoerungen()
            print(f"✅ Scraping erfolgreich: {len(stoerungen)} Störungen gefunden")
            last_check_time = datetime.now()
            current_ids = {s["id"] for s in stoerungen}

            # ✅ Beendete Störungen
            for sid, details in list(last_stoerungen.items()):
                if sid not in current_ids:
                    msg = f"✅ Beendet:\n🆔 {sid}\n📍 {details['ort']}\n🚦 {details['wirkung']}\n📋 {details['ursache']}"
                    if channel:
                        await safe_send(channel, msg)
                    if POST_TO_X:
                        await post_to_x(msg)
                    del last_stoerungen[sid]

            # 🚨 Neue Störungen
            for s in stoerungen:
                if s["id"] not in last_stoerungen:
                    last_stoerungen[s["id"]] = s
                    msg = f"🚨 Neu:\n🆔 {s['id']}\n📍 {s['ort']}\n🚦 {s['wirkung']}\n📋 {s['ursache']}"
                    if channel:
                        await safe_send(channel, msg)
                    if POST_TO_X:
                        await post_to_x(msg)

        except Exception:
            print("❌ Fehler im Loop:")
            traceback.print_exc()

        await asyncio.sleep(600)

# 🚀 Bot bereit
@bot.event
async def on_ready():
    print("✅ Bot ist bereit")
    bot.loop.create_task(check_loop())

# 📊 Status-Command
@bot.command()
async def status(ctx):
    if last_check_time:
        await ctx.send(f"⏱️ Letzte Prüfung: {last_check_time.strftime('%d.%m.%Y %H:%M:%S')}")
        await ctx.send(f"📊 Aktive Störungen: {len(last_stoerungen)}")
    else:
        await ctx.send("Noch keine Prüfung durchgeführt.")

# 🧵 Startpunkt
async def main():
    await asyncio.gather(start_web_server(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    asyncio.run(main())

