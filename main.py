import os
import asyncio
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import discord
from discord.ext import commands
from aiohttp import web  # <- Mini-Webserver für Render

# Token und Channel-ID aus Umgebungsvariablen laden
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# Mini-Webserver für Render (um Ports zu binden)
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
    print(f"🌐 Fake-Webserver läuft auf Port {port}")

# Discord-Bot mit passenden Intents starten
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_stoerungen = set()

async def scrape_stoerungen():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto("https://strecken-info.de/", timeout=60000)
            await page.wait_for_selector("div.freiefahrt-1knyh61", timeout=30000)
            html = await page.content()
            await browser.close()

            soup = BeautifulSoup(html, "html.parser")
            stoerungen = []

            for div in soup.select("div.freiefahrt-1knyh61"):
                titel_el = div.select_one("div.freiefahrt-1g6bf03")
                titel = titel_el.text.strip() if titel_el else "Keine Info"

                beschr_el = div.select_one("div.freiefahrt-12znh6")
                beschreibung = beschr_el.text.strip() if beschr_el else "Keine Beschreibung"

                unique_id = titel + beschreibung

                stoerungen.append({
                    "titel": titel,
                    "beschreibung": beschreibung,
                    "unique_id": unique_id
                })

            return stoerungen

    except Exception as e:
        print(f"[{datetime.now()}] ❌ Fehler beim Scrapen: {e}")
        return []

@bot.event
async def on_ready():
    print(f"🤖 Bot ist online als {bot.user}")
    bot.loop.create_task(check_stoerungen())

async def check_stoerungen():
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"❌ Channel mit ID {CHANNEL_ID} nicht gefunden!")
        return

    global last_stoerungen

    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()
        if not stoerungen:
            print(f"[{datetime.now()}] ⚠️ Keine neuen Störungen gefunden.")
        else:
            for s in stoerungen:
                if s["unique_id"] not in last_stoerungen:
                    last_stoerungen.add(s["unique_id"])
                    nachricht = f"🚨 **Störung:** {s['titel']}\n{s['beschreibung']}"
                    try:
                        await channel.send(nachricht)
                        print(f"[{datetime.now()}] ✅ Neue Störung gesendet.")
                    except Exception as e:
                        print(f"❌ Fehler beim Senden an Discord: {e}")
        await asyncio.sleep(600)

async def main():
    if DISCORD_TOKEN is None or CHANNEL_ID == 0:
        print("❌ DISCORD_TOKEN oder CHANNEL_ID sind nicht gesetzt!")
        return

    await asyncio.gather(
        start_web_server(),  # <- Webserver starten
        bot.start(DISCORD_TOKEN)  # <- Bot starten
    )

if __name__ == "__main__":
    asyncio.run(main())

