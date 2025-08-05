import os
import asyncio
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import discord
from discord.ext import commands
from aiohttp import web

# 🔐 Discord-Variablen
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# 🌐 Healthcheck für Render & UptimeRobot
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

# 📣 Discord-Bot starten
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_stoerungen = set()

# 🔍 Scraper – verbessert
async def scrape_stoerungen():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            print("🌐 Lade strecken-info.de...")
            await page.goto("https://strecken-info.de/", timeout=60000)
            await page.wait_for_selector("div[class*='freiefahrt']", timeout=30000)
            html = await page.content()
            await browser.close()

            soup = BeautifulSoup(html, "html.parser")
            stoerungen = []

            for div in soup.select("div[class*='freiefahrt']"):
                text = div.get_text(strip=True, separator=" ")

                # Debug-Ausgabe (optional, kann gelöscht werden)
                # print(f"🧪 Gefundener Text: {text}")

                # Diese Inhalte ignorieren – keine echten Störungen!
                if (
                    not text 
                    or len(text) < 30 
                    or "Keine Daten gefunden" in text 
                    or "OpenStreetMap" in text 
                    or "Filter" in text
                ):
                    continue

                titel = text.split(".")[0][:100]
                beschreibung = text
                unique_id = titel + beschreibung

                stoerungen.append({
                    "titel": titel,
                    "beschreibung": beschreibung,
                    "unique_id": unique_id
                })

            print(f"[{datetime.now()}] 🔍 {len(stoerungen)} echte Störungen gefunden.")
            return stoerungen

    except Exception as e:
        print(f"[{datetime.now()}] ❌ Fehler beim Scrapen: {e}")
        return []

# 📥 Bot ready
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

                    beschreibung_formatiert = s['beschreibung'].replace(". ", ".\n")
                    nachricht = (
                        "🚨 **Neue Bahn-Störung entdeckt!**\n\n"
                        f"**Titel:** {s['titel']}\n\n"
                        f"**Details:**\n{beschreibung_formatiert}"
                    )

                    try:
                        await channel.send(nachricht)
                        print(f"[{datetime.now()}] ✅ Neue Störung gesendet.")
                    except Exception as e:
                        print(f"❌ Fehler beim Senden an Discord: {e}")

        await asyncio.sleep(600)  # alle 10 Minuten prüfen

# 🔁 Hauptfunktion
async def main():
    if DISCORD_TOKEN is None or CHANNEL_ID == 0:
        print("❌ DISCORD_TOKEN oder CHANNEL_ID sind nicht gesetzt!")
        return

    await asyncio.gather(
        start_web_server(),
        bot.start(DISCORD_TOKEN)
    )

if __name__ == "__main__":
    asyncio.run(main())
