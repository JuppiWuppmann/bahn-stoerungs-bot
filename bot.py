import os
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright
import discord
from discord.ext import commands
from aiohttp import web
from io import BytesIO

# 🔐 Umgebungsvariablen (aus .env bei Render)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = os.getenv("ADMIN_ID")
DEBUG = os.getenv("DEBUG", "true").lower() == "true"  # 🐞 DEBUG-Modus aktivieren

# 🌐 Healthcheck-Handler
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

# 📣 Discord-Bot Setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_stoerungen = set()
last_check_time = None

# 📸 Screenshot senden bei Fehler
async def send_screenshot(page, fehlertext="Fehler"):
    try:
        channel = await bot.fetch_channel(CHANNEL_ID)
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

# 🔍 Scraping-Funktion
async def scrape_stoerungen():
    print(f"[{datetime.now()}] 🔁 scrape_stoerungen gestartet")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1280, "height": 1024})
            page = await context.new_page()
            await page.goto("https://strecken-info.de/", timeout=60000)
            print("🌐 Website geladen.")

            # Infofenster schließen
            try:
                await page.wait_for_selector("button:has-text('X')", timeout=7000)
                close_btn = await page.query_selector("button:has-text('X')")
                if close_btn:
                    await close_btn.click()
                    print("✅ Info-Fenster geschlossen.")
            except:
                print("⚠️ Kein Info-Fenster oder bereits geschlossen.")

            # Filter öffnen
            try:
                await page.wait_for_selector("button:has-text('Filter')", timeout=10000)
                await page.click("button:has-text('Filter')")
                await asyncio.sleep(1)
                print("✅ Filter-Menü geöffnet.")
            except Exception as e:
                print("❌ Fehler beim Öffnen des Filter-Menüs:", e)
                await send_screenshot(page, "Fehler beim Öffnen des Filters")
                return []

            # Filter deaktivieren
            for label_text in ["Baustellen", "Streckenruhen"]:
                try:
                    label = await page.query_selector(f"label:has-text('{label_text}')")
                    if label:
                        checkbox = await label.query_selector("input[type='checkbox']")
                        if checkbox and await checkbox.is_checked():
                            await checkbox.click()
                            print(f"✅ '{label_text}' deaktiviert.")
                        else:
                            print(f"☑️ '{label_text}' war bereits deaktiviert.")
                except Exception as e:
                    print(f"⚠️ Fehler beim Deaktivieren von {label_text}:", e)

            # Einschränkungen öffnen
            try:
                await page.click("text=Einschränkungen", timeout=10000)
                print("✅ Einschränkungen geöffnet.")
            except Exception as e:
                print("❌ Fehler beim Öffnen des Tabs:", e)
                await send_screenshot(page, "Fehler beim Öffnen des Tabs")
                return []

            # Tabelle laden
            try:
                await page.wait_for_selector("table tbody tr", timeout=20000)
                print("✅ Tabelle gefunden.")
            except Exception as e:
                print("❌ Tabelle nicht gefunden:", e)
                await send_screenshot(page, "Tabelle nicht gefunden")
                return []

            rows = await page.query_selector_all("table tbody tr")
            print(f"🔍 Gefundene Zeilen: {len(rows)}")

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

                typ_klein = typ.strip().lower()
                if typ_klein in ["baustelle", "streckenruhe"]:
                    continue

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

                print(f"📋 Erkannt: ID {id_text.strip()} | Typ: {typ.strip()}")
                stoerungen.append({
                    "unique_id": id_text.strip(),
                    "nachricht": nachricht
                })

            print(f"[{datetime.now()}] ✅ {len(stoerungen)} relevante Störungen erkannt.")
            return stoerungen

    except Exception as e:
        print(f"❌ Fehler in scrape_stoerungen(): {e}")
        return []

# 🤖 Bot Ready
@bot.event
async def on_ready():
    print(f"🤖 Bot läuft als {bot.user}")
    channel = await bot.fetch_channel(CHANNEL_ID)
    print(f"📡 Channel geladen: {channel}")
    if channel:
        await channel.send("✅ Bahn-Störungs-Bot wurde gestartet!")
    bot.loop.create_task(check_stoerungen(channel))

# 🔁 Prüfungsschleife
async def check_stoerungen(channel):
    global last_stoerungen, last_check_time
    await bot.wait_until_ready()
    print("🚀 Prüfung gestartet")

    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()
        last_check_time = datetime.now()

        if not stoerungen:
            print("ℹ️ Keine neuen Störungen gefunden.")
            await asyncio.sleep(600)
            continue

        for s in stoerungen:
            # 🐞 DEBUG: Sende alles oder nur neue?
            if DEBUG or s["unique_id"] not in last_stoerungen:
                last_stoerungen.add(s["unique_id"])
                try:
                    await channel.send(s["nachricht"])
                    print(f"✅ Störung gesendet: {s['unique_id']}")
                except Exception as e:
                    print(f"❌ Fehler beim Senden: {e}")
            else:
                print(f"⏩ Bereits gesendet: {s['unique_id']}")

        await asyncio.sleep(600)

# ▶️ Main
async def main():
    if not DISCORD_TOKEN or CHANNEL_ID == 0:
        print("❌ Umgebungsvariablen fehlen!")
        return
    await asyncio.gather(
        start_web_server(),
        bot.start(DISCORD_TOKEN)
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot beendet.")
