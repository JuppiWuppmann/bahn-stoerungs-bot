import os
import asyncio
from datetime import datetime
from discord.ext import commands
import discord
from aiohttp import web
from playwright.async_api import async_playwright
from io import BytesIO

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = os.getenv("ADMIN_ID")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_stoerungen = set()
last_check_time = None


# --- Healthcheck ---
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


# --- Screenshot senden ---
async def send_screenshot(page, fehlertext="Fehler"):
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        screenshot_bytes = await page.screenshot(type="png")
        buffer = BytesIO(screenshot_bytes)
        buffer.name = "screenshot.png"
        buffer.seek(0)
        await channel.send(
            content=f"❌ **Fehler beim Scraping:** {fehlertext}",
            file=discord.File(fp=buffer, filename="screenshot.png")
        )


# --- Overlays schließen ---
async def ensure_no_overlays(page, max_wait=15000):
    start_time = datetime.now()

    while True:
        closed_any = False

        # Cookie-/Analyse-Banner
        try:
            ablehnen_btn = await page.query_selector("button:has-text('Ablehnen')")
            if ablehnen_btn:
                await ablehnen_btn.click()
                await asyncio.sleep(0.8)
                print("✅ Cookie-/Analyse-Banner abgelehnt")
                closed_any = True
        except:
            pass

        # Alle "Schließen"-Buttons
        try:
            close_buttons = await page.query_selector_all("button[aria-label='Schließen']")
            for btn in close_buttons:
                await btn.click()
                await asyncio.sleep(0.8)
                print("✅ Overlay geschlossen")
                closed_any = True
        except:
            pass

        if (datetime.now() - start_time).total_seconds() * 1000 > max_wait:
            print("⚠️ Overlay-Entfernung abgebrochen (Zeitlimit erreicht)")
            break

        if not closed_any:
            break


# --- Haupt-Scraping ---
async def scrape_stoerungen():
    global last_stoerungen
    print(f"[{datetime.now()}] 🔁 scrape_stoerungen gestartet")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto("https://strecken-info.de/", timeout=60000)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            # Overlays schließen
            await ensure_no_overlays(page)

            # Tabelle warten
            await page.wait_for_selector("table tbody tr", timeout=15000)
            rows = page.locator("table tbody tr")
            row_count = await rows.count()
            print(f"Gefundene Zeilen: {row_count}")

            new_stoerungen = []
            for i in range(row_count):
                cells = rows.nth(i).locator("td")
                cell_count = await cells.count()
                cell_texts = [await cells.nth(j).inner_text() for j in range(cell_count)]
                print(f"Zeile {i}: {cell_texts}")

                if len(cell_texts) < 8:
                    continue

                id_text = cell_texts[0].strip()
                typ = cell_texts[1].strip()
                ort = cell_texts[2].strip()
                region = cell_texts[3].strip()
                wirkung = cell_texts[4].strip()
                ursache = cell_texts[5].strip()
                gueltig_von = cell_texts[6].strip()
                gueltig_bis = cell_texts[7].strip()

                # Baustellen & Streckenruhen ignorieren
                if typ.lower() in ["baustelle", "streckenruhe"]:
                    continue

                if id_text not in last_stoerungen:
                    message = (
                        "🚨 **Neue Bahn-Störung entdeckt!**\n\n"
                        f"🆔 **ID:** {id_text}\n"
                        f"📌 **Typ:** {typ}\n"
                        f"📍 **Ort:** {ort}\n"
                        f"🗺️ **Region:** {region}\n"
                        f"🚦 **Wirkung:** {wirkung}\n"
                        f"📋 **Ursache:** {ursache}\n"
                        f"⏰ **Gültigkeit:** {gueltig_von} → {gueltig_bis}"
                    )
                    new_stoerungen.append({"id": id_text, "text": message})

            print(f"🔍 Neue Störungen: {len(new_stoerungen)}")
            return new_stoerungen

    except Exception as e:
        print(f"❌ Fehler beim Scraping: {e}")
        return []


# --- Check-Loop ---
async def check_stoerungen():
    global last_stoerungen, last_check_time
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)

    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()
        last_check_time = datetime.now()

        for s in stoerungen:
            if s["id"] not in last_stoerungen:
                last_stoerungen.add(s["id"])
                await channel.send(s["text"])

        await asyncio.sleep(600)  # alle 10 Minuten


# --- Status-Befehl ---
@bot.command()
async def status(ctx):
    if ADMIN_ID and str(ctx.author.id) != str(ADMIN_ID):
        await ctx.send("❌ Du bist nicht berechtigt.")
        return
    if last_check_time:
        await ctx.send(f"✅ Letzte Prüfung: {last_check_time.strftime('%d.%m.%Y %H:%M:%S')}")
    else:
        await ctx.send("⏳ Noch keine Prüfung erfolgt.")


@bot.event
async def on_ready():
    print(f"✅ Bot gestartet als {bot.user}")
    bot.loop.create_task(check_stoerungen())


# --- Start ---
async def main():
    await asyncio.gather(
        start_web_server(),
        bot.start(DISCORD_TOKEN)
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot wurde beendet.")
