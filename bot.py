import os
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright
import discord
from discord.ext import commands
from aiohttp import web

# 🔐 Discord
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# 🌐 Render/UptimeRobot Healthcheck
async def handle_health(request):
    return web.Response(text="OK")

async def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/healthz", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Webserver läuft auf Port {port}")

# 📣 Discord Bot Setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_stoerungen = set()

# 🔍 Scraper (Tabelle auslesen und formatieren)
async def scrape_stoerungen():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            print("🌐 Öffne streckeninfo.de ...")
            await page.goto("https://streckeninfo.de/", timeout=60000)

            # Auf "Einschränkungen" klicken
            await page.click("text=Einschränkungen")
            await page.wait_for_selector("table", timeout=30000)

            # Tabelle auslesen
            rows = await page.query_selector_all("table tbody tr")
            stoerungen = []

            for row in rows:
                columns = await row.query_selector_all("td")
                if len(columns) < 8:
                    continue

                id_text = await columns[0].inner_text()
                typ = await columns[1].inner_text()
                ort = await columns[2].inner_text()
                region = await columns[3].inner_text()
                wirkung = await columns[4].inner_text()
                ursache = await columns[5].inner_text()
                gueltig_von = await columns[6].inner_text()
                gueltig_bis = await columns[7].inner_text()

                unique_id = id_text.strip()

                nachricht = (
                    "🚨 **Neue Bahn-Störung entdeckt!**\n\n"
                    f"🆔 **ID:** {id_text.strip()}\n"
                    f"📌 **Typ:** {typ.strip()}\n"
                    f"📍 **Ort:** {ort.strip()}\n"
                    f"🗺️ **Region:** {region.strip()}\n"
                    f"🚦 **Wirkung:** {wirkung.strip()}\n"
                    f"📋 **Ursache:** {ursache.strip()}\n"
                    f"⏰ **Gültigkeit:** {gueltig_von.strip()} → {gueltig_bis.strip()}"
                )

                stoerungen.append({
                    "unique_id": unique_id,
                    "nachricht": nachricht
                })

            print(f"[{datetime.now()}] 🔍 {len(stoerungen)} Störungen gefunden.")
            return stoerungen

    except Exception as e:
        print(f"[{datetime.now()}] ❌ Fehler beim Scrapen: {e}")
        return []

# 🤖 Bot ready
@bot.event
async def on_ready():
    print(f"🤖 Bot ist online als {bot.user}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("✅ Bahn-Störungs-Bot wurde gestartet!")
    else:
        print("❌ Channel nicht gefunden!")
    bot.loop.create_task(check_stoerungen())

# 🔁 Störungen überwachen
async def check_stoerungen():
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print("❌ Discord-Channel nicht gefunden!")
        return

    global last_stoerungen

    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()

        for s in stoerungen:
            if s["unique_id"] not in last_stoerungen:
                last_stoerungen.add(s["unique_id"])

                try:
                    await channel.send(s["nachricht"])
                    print(f"[{datetime.now()}] ✅ Neue Störung gesendet: {s['unique_id']}")
                except Exception as e:
                    print(f"❌ Fehler beim Senden: {e}")

        await asyncio.sleep(600)  # 10 Minuten warten

# 🧠 Hauptfunktion
async def main():
    if DISCORD_TOKEN is None or CHANNEL_ID == 0:
        print("❌ DISCORD_TOKEN oder CHANNEL_ID fehlen!")
        return

    await asyncio.gather(
        start_web_server(),
        bot.start(DISCORD_TOKEN)
    )

if __name__ == "__main__":
    asyncio.run(main())
