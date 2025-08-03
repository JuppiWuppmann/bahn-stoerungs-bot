import os
import asyncio
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import discord
from discord.ext import commands

# Token und Channel-ID aus Umgebungsvariablen laden
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

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

            # Warte auf mind. eine Art von Störung
            await page.wait_for_selector("div.freiefahrt-1knyh61, div.freiefahrt-1lyxvt5", timeout=30000)

            html = await page.content()
            await browser.close()

            soup = BeautifulSoup(html, "html.parser")
            stoerungen = []

            # Großstörungen
            for div in soup.select("div.freiefahrt-1knyh61"):
                text = div.get_text(strip=True)
                if text:
                    stoerungen.append({
                        "titel": "Großstörung",
                        "beschreibung": text,
                        "unique_id": f"gross_{text}"
                    })

            # Streckenstörungen
            for div in soup.select("div.freiefahrt-1lyxvt5"):
                text = div.get_text(strip=True)
                if text:
                    stoerungen.append({
                        "titel": "Streckenstörung",
                        "beschreibung": text,
                        "unique_id": f"strecke_{text}"
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
                    nachricht = f"🚨 **{s['titel']}**\n{s['beschreibung']}"
                    try:
                        await channel.send(nachricht)
                        print(f"[{datetime.now()}] ✅ Neue Störung gesendet.")
                    except Exception as e:
                        print(f"❌ Fehler beim Senden an Discord: {e}")
        
        await asyncio.sleep(600)  # alle 10 Minuten prüfen

async def main():
    if DISCORD_TOKEN is None or CHANNEL_ID == 0:
        print("❌ DISCORD_TOKEN oder CHANNEL_ID sind nicht gesetzt!")
        return
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
