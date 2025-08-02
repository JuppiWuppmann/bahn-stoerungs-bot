import os
import asyncio
from discord.ext import commands
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0))  # Discord Channel ID als int

bot = commands.Bot(command_prefix="!")

async def scrape_stoerungen():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto("https://freiefahrt.bahn.de/stoerungen")  # Beispiel-URL

        # Warte auf Hauptcontainer mit Störungen (muss an die echte Website angepasst werden)
        await page.wait_for_selector("div.freiefahrt-1z3...")  # Selector anpassen!

        content = await page.content()
        await browser.close()

        soup = BeautifulSoup(content, "html.parser")
        stoerungen = []

        # Beispiel: Alle Störungsmeldungen finden (Selector anpassen!)
        for div in soup.select("div.freiefahrt-1knyh61"):  
            titel = div.select_one("div.titelklasse").text.strip()  # anpassen
            beschreibung = div.select_one("div.beschreibungklasse").text.strip()  # anpassen
            stoerungen.append(f"🚨 **{titel}**\n{beschreibung}")

        return stoerungen

@bot.event
async def on_ready():
    print(f"🤖 Bot online als {bot.user}!")

    if CHANNEL_ID:
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            stoerungen = await scrape_stoerungen()
            if stoerungen:
                for meldung in stoerungen:
                    await channel.send(meldung)
            else:
                await channel.send("Keine neuen Störungen gefunden.")
        else:
            print("⚠️ Channel nicht gefunden!")

async def main():
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
