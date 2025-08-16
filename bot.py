# BAHN-STÖRUNGS-BOT – optimierte Version mit Discord + X

import os
import asyncio
from datetime import datetime
from discord.ext import commands
import discord
from aiohttp import web
from playwright.async_api import async_playwright

# 🔑 Tokens aus Render-Umgebung
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = os.getenv("ADMIN_ID")
X_USERNAME = os.getenv("X_USERNAME")
X_PASSWORD = os.getenv("X_PASSWORD")

# Playwright Timeouts
PAGE_LOAD_TIMEOUT = 80000
CLICK_TIMEOUT = 20000

# Discord Setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Speicher
last_stoerungen = {}
last_check_time = None

# Healthcheck
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

# Discord senden
async def safe_send(channel, content):
    try:
        await channel.send(content)
    except Exception as e:
        print("⚠️ Fehler beim Senden:", e)

# Scraper
async def scrape_stoerungen():
    browser, context = None, None
    results = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1280, "height": 800})
            page = await context.new_page()
            await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)
            await page.wait_for_load_state("networkidle")

            # 2x sortieren nach "Gültigkeit von"
            for _ in range(2):
                try:
                    el = await page.wait_for_selector('th:has-text("Gültigkeit von")', timeout=CLICK_TIMEOUT)
                    await el.click()
                    await asyncio.sleep(0.3)
                except:
                    pass

            rows = await page.query_selector_all("table tbody tr")
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
                gueltig_bis = (await cols[7].inner_text()).strip()

                if typ.lower() in ["baustelle", "streckenruhe"]:
                    continue

                try:
                    gueltig_bis_dt = datetime.strptime(gueltig_bis, "%d.%m.%Y %H:%M")
                except:
                    gueltig_bis_dt = None

                results.append({
                    "id": id_text,
                    "typ": typ,
                    "ort": ort,
                    "region": region,
                    "wirkung": wirkung,
                    "ursache": ursache,
                    "gueltig_von": gueltig_von,
                    "gueltig_bis": gueltig_bis_dt
                })

        await context.close()
        await browser.close()
    except Exception as e:
        print("❌ Scraping Fehler:", e)
        try:
            if context: await context.close()
            if browser: await browser.close()
        except:
            pass
    return results

# Posting zu X
async def post_to_x(text):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state="x_storage.json") if os.path.exists("x_storage.json") else await browser.new_context()
            page = await context.new_page()

            if not os.path.exists("x_storage.json"):
                await page.goto("https://x.com/login")
                await page.fill('input[name="text"]', X_USERNAME)
                await page.keyboard.press("Enter")
                await asyncio.sleep(2)
                await page.fill('input[name="password"]', X_PASSWORD)
                await page.keyboard.press("Enter")
                await asyncio.sleep(5)
                await context.storage_state(path="x_storage.json")

            await page.goto("https://x.com/compose/tweet")
            await page.fill('div[role="textbox"]', text[:270])  # Länge begrenzen
            await page.keyboard.press("Control+Enter")
            await asyncio.sleep(3)

            await context.close()
            await browser.close()
    except Exception as e:
        print("❌ X Posting Fehler:", e)

# Hauptschleife
async def check_stoerungen():
    global last_stoerungen, last_check_time
    channel = bot.get_channel(CHANNEL_ID)

    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()
        last_check_time = datetime.now()
        current_ids = {s["id"] for s in stoerungen}

        # Beendete
        for sid, details in list(last_stoerungen.items()):
            if sid not in current_ids or (details["gueltig_bis"] and details["gueltig_bis"] < datetime.now()):
                msg = f"""✅ **Bahn-Störung behoben!**
🆔 **ID:** {sid}
📍 **Ort:** {details['ort']}
🚦 **Wirkung:** {details['wirkung']}
📋 **Ursache:** {details['ursache']}
⏰ **Dauer:** {details['gueltig_von']} → {details['gueltig_bis'].strftime('%d.%m.%Y %H:%M') if details['gueltig_bis'] else 'unbekannt'}"""
                if channel: await safe_send(channel, msg)
                await post_to_x(f"✅ Störung behoben!\nID: {sid}\nOrt: {details['ort']}\nWirkung: {details['wirkung']}\nUrsache: {details['ursache']}")
                del last_stoerungen[sid]

        # Neue
        for s in stoerungen:
            if s["id"] not in last_stoerungen:
                last_stoerungen[s["id"]] = s
                msg = f"""🚨 **Neue Bahn-Störung!**
🆔 **ID:** {s['id']}
📍 **Ort:** {s['ort']}
🚦 **Wirkung:** {s['wirkung']}
📋 **Ursache:** {s['ursache']}
⏰ **Von:** {s['gueltig_von']}"""
                if channel: await safe_send(channel, msg)
                await post_to_x(f"🚨 Neue Störung!\nID: {s['id']}\nOrt: {s['ort']}\nWirkung: {s['wirkung']}\nUrsache: {s['ursache']}")

        await asyncio.sleep(600)  # alle 10 Minuten prüfen

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
    print(f"🤖 Bot läuft als {bot.user}")
    bot.loop.create_task(check_stoerungen())

async def main():
    await asyncio.gather(start_web_server(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    asyncio.run(main())
