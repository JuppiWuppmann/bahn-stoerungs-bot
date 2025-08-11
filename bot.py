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
    print("🔍 Starte Overlay-Entfernung...")
    start_time = datetime.now()

    while True:
        closed_any = False

        # 🔹 Usercentrics-Overlay inkl. Notfall-Remove
        try:
            uc_overlay = await page.query_selector("#usercentrics-cmp-ui")
            if uc_overlay:
                print("⚠️ Usercentrics Overlay gefunden! Versuche zu schließen...")
                ablehnen_btn = await page.query_selector("button:has-text('Ablehnen')")
                akzeptieren_btn = await page.query_selector("button:has-text('Alles akzeptieren')")

                if ablehnen_btn:
                    await ablehnen_btn.click()
                    await asyncio.sleep(1)
                    print("✅ Usercentrics Overlay per Klick geschlossen")
                    closed_any = True
                elif akzeptieren_btn:
                    await akzeptieren_btn.click()
                    await asyncio.sleep(1)
                    print("✅ Usercentrics Overlay per Klick geschlossen")
                    closed_any = True
                else:
                    # Fallback: DOM-Element komplett entfernen
                    await page.evaluate("""
                        const el = document.querySelector('#usercentrics-cmp-ui');
                        if (el) el.remove();
                    """)
                    await asyncio.sleep(0.5)
                    print("🛠️ Usercentrics Overlay per JS entfernt")
                    closed_any = True
        except Exception as e:
            print(f"⚠️ Fehler bei Usercentrics-Check: {e}")

        # 🔹 Cookie-/Analyse-Banner
        try:
            ablehnen_btn = await page.query_selector("button:has-text('Ablehnen')")
            if ablehnen_btn:
                await ablehnen_btn.click()
                await asyncio.sleep(0.8)
                print("✅ Cookie-/Analyse-Banner abgelehnt")
                closed_any = True
        except:
            pass

        # 🔹 Allgemeine Schließen-Buttons
        try:
            close_buttons = await page.query_selector_all("button[aria-label='Schließen']")
            for btn in close_buttons:
                await btn.click()
                await asyncio.sleep(0.8)
                print("✅ Anderes Overlay geschlossen")
                closed_any = True
        except:
            pass

        if (datetime.now() - start_time).total_seconds() * 1000 > max_wait:
            print("⚠️ Overlay-Entfernung abgebrochen (Zeitlimit erreicht)")
            break

        if not closed_any:
            print("ℹ️ Keine weiteren Overlays gefunden")
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

            # Overlays schließen (direkt nach dem Laden!)
            await ensure_no_overlays(page)

            # Filter öffnen
            try:
                await ensure_no_overlays(page)
                toggle_button = await page.query_selector("button[aria-label='Filter öffnen']")
                if not toggle_button:
                    toggle_button = await page.query_selector("button:has-text('Filter')")
                if toggle_button:
                    await toggle_button.click()
                    await asyncio.sleep(2)
                    print("✅ Filter geöffnet")
                else:
                    raise Exception("Filter-Button nicht gefunden")
            except Exception as e:
                await send_screenshot(page, f"Filter-Panel konnte nicht geöffnet werden: {e}")
                return []

            # Baustellen & Streckenruhen ausschalten
            for label_text in ["Baustellen", "Streckenruhen"]:
                try:
                    label = await page.query_selector(f"label:has-text('{label_text}')")
                    if label:
                        checkbox = await label.query_selector("input[type='checkbox']")
                        if checkbox and await checkbox.is_checked():
                            await checkbox.click()
                            await asyncio.sleep(0.5)
                            print(f"✅ {label_text} deaktiviert")
                except Exception as e:
                    print(f"⚠️ Konnte {label_text} nicht deaktivieren: {e}")

            # Einschränkungen aktivieren
            try:
                await ensure_no_overlays(page)
                await page.click("text=Einschränkungen")
                await asyncio.sleep(2)
                print("✅ Einschränkungen aktiviert")
            except Exception as e:
                await send_screenshot(page, f"Einschränkungen konnten nicht aktiviert werden: {e}")
                return []

            # Tabelle sortieren
            try:
                sort_button = await page.wait_for_selector('th:has-text("Gültigkeit von")', timeout=5000)
                await sort_button.click()
                await asyncio.sleep(0.5)
                await sort_button.click()
                await asyncio.sleep(1)
                print("✅ Tabelle sortiert")
            except Exception as e:
                await send_screenshot(page, f"Sortierung fehlgeschlagen: {e}")
                return []

            # Tabelle lesen
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
