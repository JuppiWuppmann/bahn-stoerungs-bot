import os
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright
import discord
from discord.ext import commands
from aiohttp import web
from io import BytesIO

# 🔐 Discord
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = os.getenv("ADMIN_ID")  # optional

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
last_check_time = None

# 📸 Screenshot-Funktion
async def send_screenshot(page, fehlertext="Fehler"):
    try:
        channel = bot.get_channel(CHANNEL_ID)
        if channel is None:
            print("⚠️ Screenshot nicht gesendet – Channel nicht gefunden.")
            return

        screenshot_bytes = await page.screenshot(type="png")
        buffer = BytesIO(screenshot_bytes)
        buffer.name = "screenshot.png"
        buffer.seek(0)

        await channel.send(
            content=f"❌ **Fehler beim Scraping:** {fehlertext}",
            file=discord.File(fp=buffer, filename="screenshot.png")
        )
    except Exception as e:
        print("⚠️ Fehler beim Screenshot-Senden:", e)

# 🔍 Scraper
async def scrape_stoerungen():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 1024})
            page = await context.new_page()
            print("🌐 Öffne strecken-info.de ...")
            await page.goto("https://strecken-info.de/", timeout=60000)

            # Pop-up "Züge rollen" schließen
            try:
                close_button = await page.query_selector("button:has-text('OK')")
                if close_button:
                    await close_button.click()
                    print("✅ 'Züge rollen'-Pop-up geschlossen.")
            except Exception as e:
                print("⚠️ Kein Pop-up oder Fehler beim Schließen:", e)

            # Einschränkungen-Tab klicken
            try:
                await page.click("text=Einschränkungen", timeout=10000)
                print("✅ Einschränkungen-Tab geöffnet.")
            except Exception as e:
                print("❌ Fehler beim Klicken auf 'Einschränkungen':", e)
                await send_screenshot(page, "Fehler beim Tab-Klick")
                return []

            # Checkbox "Nur Kartenausschnitt" deaktivieren
            try:
                checkbox = await page.query_selector("input[type='checkbox']")
                if checkbox:
                    is_checked = await checkbox.is_checked()
                    if is_checked:
                        await checkbox.click()
                        print("✅ 'Nur Kartenausschnitt' deaktiviert.")
            except Exception as e:
                print("⚠️ Checkbox-Problem:", e)

            # Tabelle laden
            try:
                await page.wait_for_selector("table tbody tr", timeout=20000)
                print("✅ Tabelle erfolgreich geladen.")
            except Exception as e:
                print("❌ Tabelle nicht gefunden:", e)
                await send_screenshot(page, "Tabelle nicht gefunden")
                return []

            rows = await page.query_selector_all("table tbody tr")
            print(f"🔍 Anzahl Tabellenzeilen: {len(rows)}")

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

            print(f"[{datetime.now()}] ✅ {len(stoerungen)} Störungen erkannt.")
            return stoerungen

    except Exception as e:
        print(f"[{datetime.now()}] ❌ Schwerer Fehler beim Scrapen: {e}")
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

# 🔁 Prüfungsschleife
async def check_stoerungen():
    global last_check_time
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print("❌ Channel nicht gefunden!")
        return

    global last_stoerungen

    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()
        last_check_time = datetime.now()

        for s in stoerungen:
            if s["unique_id"] not in last_stoerungen:
                last_stoerungen.add(s["unique_id"])
                try:
                    await channel.send(s["nachricht"])
                    print(f"[{datetime.now()}] ✅ Neue Störung gesendet: {s['unique_id']}")
                except Exception as e:
                    print(f"❌ Fehler beim Senden: {e}")

        await asyncio.sleep(600)

# 🛠️ !status Admin-Befehl
@bot.command()
async def status(ctx):
    if ADMIN_ID and str(ctx.author.id) != str(ADMIN_ID):
        await ctx.send("❌ Du bist nicht berechtigt, diesen Befehl zu verwenden.")
        return

    if last_check_time:
        await ctx.send(f"✅ Bot läuft. Letzte Prüfung: {last_check_time.strftime('%d.%m.%Y %H:%M:%S')}")
    else:
        await ctx.send("⏳ Bot wurde gestartet, aber noch keine Prüfung durchgeführt.")

# ▶️ Main
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
