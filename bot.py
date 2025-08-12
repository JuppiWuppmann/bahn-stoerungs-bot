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
async def ensure_no_overlays(page, max_wait=30000):
    print("🔍 Starte Overlay-Entfernung...")
    start_time = datetime.now()

    while True:
        closed_any = False

        # Generischer Overlay-Entferner (auch invisible)
        overlays = await page.query_selector_all("#usercentrics-cmp-ui, div[role='dialog'], div[style*='z-index']")
        for ov in overlays:
            try:
                styles = await page.evaluate(
                    "(el) => window.getComputedStyle(el).getPropertyValue('pointer-events')", ov
                )
                if styles == "none":
                    continue
                await page.evaluate("el => el.remove()", ov)
                print("🗑️ Overlay entfernt (generisch)")
                closed_any = True
            except:
                pass

        # Buttons zum Ablehnen / Schließen
        for sel in [
            "button:has-text('Ablehnen')",
            "button:has-text('Alles akzeptieren')",
            "button[aria-label='Schließen']"
        ]:
            try:
                btns = await page.query_selector_all(sel)
                for b in btns:
                    await b.click()
                    await asyncio.sleep(0.5)
                    print(f"✅ Button {sel} geklickt")
                    closed_any = True
            except:
                pass

        if (datetime.now() - start_time).total_seconds() * 1000 > max_wait:
            print("⚠️ Overlay-Entfernung abgebrochen (Zeitlimit erreicht)")
            break

        if not closed_any:
            break

# --- Sicherer Klick mit mehrfachen Versuchen + Reload ---
async def safe_click(page, selector, timeout=30000, description="Element", alt_selectors=None):
    selectors_to_try = [selector] + (alt_selectors or [])
    for attempt in range(4):  # letzter Versuch nach Reload
        try:
            await ensure_no_overlays(page)
            for sel in selectors_to_try:
                try:
                    el = await page.wait_for_selector(sel, timeout=timeout)
                    await el.click()
                    await asyncio.sleep(0.5)
                    print(f"✅ {description} geklickt mit Selector {sel} (Versuch {attempt+1})")
                    return True
                except Exception as e:
                    print(f"⚠️ {description} mit {sel} fehlgeschlagen: {e}")
            raise Exception("Alle Selektoren fehlgeschlagen")
        except Exception as e:
            print(f"⚠️ {description} Klick fehlgeschlagen (Versuch {attempt+1}): {e}")
            if attempt == 3:
                await send_screenshot(page, f"{description} konnte nicht geklickt werden: {e}")
                return False
            if attempt == 2:
                print("🔄 Letzter Versuch nach Seiten-Reload...")
                await page.reload()
                await page.wait_for_load_state("networkidle")
            await asyncio.sleep(1)
    return False

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

            # Filter öffnen (mit Fallback)
            if not await safe_click(
                page,
                "button[aria-label='Filter öffnen']",
                description="Filter öffnen",
                alt_selectors=["button:has-text('Filter')"]
            ):
                return []

            # Baustellen & Streckenruhen ausschalten
            for label_text in ["Baustellen", "Streckenruhen"]:
                try:
                    label = await page.query_selector(f"label:has-text('{label_text}')")
                    if label:
                        checkbox = await label.query_selector("input[type='checkbox']")
                        if checkbox and await checkbox.is_checked():
                            await checkbox.click()
                            await asyncio.sleep(0.3)
                            print(f"✅ {label_text} deaktiviert")
                except:
                    pass

            # Einschränkungen aktivieren
            if not await safe_click(page, "text=Einschränkungen", description="Einschränkungen aktivieren"):
                return []

            # Tabelle sortieren (mit Fallback)
            if not await safe_click(
                page,
                'th:has-text("Gültigkeit von")',
                description="Tabelle sortieren",
                alt_selectors=["table thead th:nth-last-child(2)"]
            ):
                print("⚠️ Sortierung über Fallback-Spalte")

            # Zweites Mal sortieren zur Sicherheit
            await safe_click(
                page,
                'th:has-text("Gültigkeit von")',
                description="Tabelle sortieren (zweites Mal)",
                alt_selectors=["table thead th:nth-last-child(2)"]
            )

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
