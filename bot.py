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

# --- Healthcheck-Endpunkt ---
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

# --- Screenshot senden bei Fehlern ---
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

            # 1️⃣ Cookies oder Modale schließen
            try:
                close_selectors = [
                    "button:has-text('Akzeptieren')",
                    "button[aria-label='Close']",
                    "button:has-text('Schließen')"
                ]
                for sel in close_selectors:
                    btn = await page.query_selector(sel)
                    if btn:
                        await btn.click()
                        await asyncio.sleep(1)
            except:
                pass

            # 2️⃣ Filter-Panel öffnen
            try:
                toggle_button = await page.wait_for_selector("button[aria-label='Filter öffnen']", timeout=5000)
                await toggle_button.click()
                await asyncio.sleep(1)
            except Exception as e:
                await send_screenshot(page, f"Filter-Panel konnte nicht geöffnet werden: {e}")
                return []

            # 3️⃣ Baustellen & Streckenruhen deaktivieren
            for label_text in ["Baustellen", "Streckenruhen"]:
                try:
                    label = await page.query_selector(f"label:has-text('{label_text}')")
                    if label:
                        checkbox = await label.query_selector("input[type='checkbox']")
                        if checkbox and await checkbox.is_checked():
                            await checkbox.click()
                            await asyncio.sleep(0.5)
                except:
                    pass

            # 4️⃣ Einschränkungen aktivieren
            try:
                einschr = await page.query_selector("label:has-text('Einschränkungen')")
                if einschr:
                    checkbox = await einschr.query_selector("input[type='checkbox']")
                    if checkbox and not await checkbox.is_checked():
                        await checkbox.click()
                        await asyncio.sleep(0.5)
            except:
                pass

            # 5️⃣ Filter-Panel schließen (optional)
            try:
                close_filter = await page.query_selector("button[aria-label='Filter schließen']")
                if close_filter:
                    await close_filter.click()
                    await asyncio.sleep(1)
            except:
                pass

            # 6️⃣ Sortieren nach "Gültigkeit von"
            try:
                sort_button = await page.wait_for_selector('th:has-text("Gültigkeit von")', timeout=5000)
                await sort_button.click()
                await page.wait_for_timeout(500)
                await sort_button.click()
                await page.wait_for_timeout(1000)
            except Exception as e:
                await send_screenshot(page, "Sortierung fehlgeschlagen")
                print("⚠️ Sortierung fehlgeschlagen:", e)
                return []

            # 7️⃣ Tabelle auslesen
            await page.wait_for_selector("table tbody tr", timeout=15000)
            rows = await page.query_selector_all("table tbody tr")
            new_stoerungen = []

            for row in rows:
                columns = await row.query_selector_all("td")
                if len(columns) < 8:
                    continue

                id_text = (await columns[0].inner_text()).strip()
                typ = (await columns[1].inner_text()).strip()
                ort = (await columns[2].inner_text()).strip()
                region = (await columns[3].inner_text()).strip()
                wirkung = (await columns[4].inner_text()).strip()
                ursache = (await columns[5].inner_text()).strip()
                gueltig_von = (await columns[6].inner_text()).strip()
                gueltig_bis = (await columns[7].inner_text()).strip()

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
        await send_screenshot(page, f"Allgemeiner Fehler: {e}")
        print(f"❌ Fehler beim Scraping: {e}")
        return []


# --- Prüfen und an Discord senden ---
async def check_stoerungen():
    global last_stoerungen, last_check_time
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    print(f"📢 CHANNEL_ID={CHANNEL_ID}, channel={channel}")

    if channel is None:
        print("⚠️ WARNUNG: channel ist None! Bitte CHANNEL_ID in Render-Umgebungsvariablen prüfen.")

    while not bot.is_closed():
        print("\n⏳ Starte neuen Check...")
        stoerungen = await scrape_stoerungen()
        last_check_time = datetime.now()

        for s in stoerungen:
            if s["id"] not in last_stoerungen:
                last_stoerungen.add(s["id"])
                try:
                    if channel:
                        await channel.send(s["text"])
                        print(f"✅ Nachricht gesendet für ID {s['id']}")
                    else:
                        print(f"⚠️ Nachricht für {s['id']} nicht gesendet, channel=None")
                except Exception as e:
                    print(f"❌ Fehler beim Senden an Discord: {e}")

        await asyncio.sleep(600)

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
        start_web_server(),       # Health-Server starten
        bot.start(DISCORD_TOKEN)  # Discord-Bot starten
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot wurde beendet.")
