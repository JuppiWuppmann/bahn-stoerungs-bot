import os
import asyncio
from discord.ext import commands
from playwright.async_api import async_playwright

# Discord-Token aus Umgebungsvariable
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0))  # Channel ID als Zahl

bot = commands.Bot(command_prefix="!")

async def install_browsers():
    async with async_playwright() as p:
        # Nur starten, um Browser herunterzuladen/installieren
        pass

@bot.event
async def on_ready():
    print(f"🤖 Bot ist online als {bot.user}!")

    # Beispiel: Sende eine Nachricht zum Start (optional)
    if CHANNEL_ID:
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            await channel.send("Bot ist gestartet und bereit!")
        else:
            print("⚠️ Channel nicht gefunden!")

async def main():
    await install_browsers()
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
