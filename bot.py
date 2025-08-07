import os
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright
import discord
from discord.ext import commands
from aiohttp import web
from io import BytesIO

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = os.getenv("ADMIN_ID")

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

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_stoerungen = set()
last_check_time = None

async def send_screenshot(page, fehlertext="Fehler"):
    try:
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
    except Exception as e:
        print("⚠️ Fehler beim Screenshot-Senden:", e)

async def scrape_stoerungen():
    print(f"[{datetime.now()}] 🔁 scrape_stoerungen gestartet")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 1024},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            print("🌐 Lade Website...")

            await page.goto("https://strecken-info.de/", timeout=60000)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(2)

            try:
                await asyncio.sleep(1)
                modals = await page.query_selector_all("div[class*='modal'], div[class*='dialog'], div[role='dialog']")
                overlay_closed = False
                for modal in modals:
                    modal_text = (await modal.inner_text()).lower()
                    if "neue features" in modal_text or "hinweis" in modal_text:
                        print(f"ℹ️ Möglicher Overlay-Text erkannt: {modal_text[:60]}...")
                        close_button = await modal.query_selector("button:has-text('X')") or await modal.query_selector("button:has-text('Schließen')") or await modal.query_selector("button")
                        if close_button:
                            try:
                                await close_button.scroll_into_view_if_needed()
                                await asyncio.sleep(0.5)
                                await close_button.click()
                                overlay_closed = True
                                print("✅ Info-Overlay geschlossen.")
                                await asyncio.sleep(1)
                                break
                            except Exception as click_err:
                                print(f"⚠️ Klick auf Overlay-Schließen-Button fehlgeschlagen: {click_err}")
                if not overlay_closed:
                    print("🛠️ Versuche Overlay per JS zu schließen...")
                    closed = await page.evaluate("""
                        () => {
                            const btn = Array.from(document.querySelectorAll("button"))
                                .find(el => el.innerText?.toLowerCase().includes("x") || el.innerText?.toLowerCase().includes("schließen"));
                            if (btn) {
                                btn.click();
                                return true;
                            }
                            return false;
                        }
                    """)
                    if closed:
                        print("✅ Overlay per JavaScript geschlossen.")
                        await asyncio.sleep(1)
                    else:
                        print("ℹ️ Kein Overlay zum Schließen erkannt.")
            except Exception as e:
                print(f"⚠️ Fehler beim Schließen des Info-Fensters: {e}")

            await send_screenshot(page, "Seite nach goto() geladen")
            await page.evaluate("""
                document.querySelectorAll("div[class*='freiefahrt']").forEach(el => el.remove());
            """)
            print("🧹 Mögliche Overlays entfernt.")

            try:
                baustellen_label = await page.query_selector("label:has-text('Baustellen')")
                if not baustellen_label:
                    print("🔍 Filter-Menü scheint nicht offen – versuche zu öffnen...")
                    filter_button = await page.query_selector("button[aria-label='Filter']") or await page.query_selector("button:has-text('Filter')") or await page.query_selector("text=Filter")
                    if filter_button:
                        await filter_button.scroll_into_view_if_needed()
                        await asyncio.sleep(1)
                        await filter_button.click()
                        await asyncio.sleep(1)
                        print("✅ Filter-Menü geöffnet.")
                    else:
                        print("❌ Kein 'Filter'-Button gefunden.")
                        await send_screenshot(page, "Filter-Button nicht gefunden")
                        return []
                else:
                    print("✅ Filter-Menü ist bereits offen.")
            except Exception as e:
                print("⚠️ Fehler beim Öffnen oder Erkennen des Filter-Menüs:", e)
                await send_screenshot(page, "Fehler beim Öffnen des Filters")
                return []

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

            try:
                await page.click("text=Einschränkungen", timeout=10000)
                print("✅ Einschränkungen geöffnet.")
            except Exception as e:
                print("❌ Fehler beim Klick auf Einschränkungen:", e)
                await send_screenshot(page, "Fehler beim Tab-Klick")
                return []

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

                stoerungen.append({
                    "unique_id": id_text.strip(),
                    "nachricht": nachricht
                })

            print(f"[{datetime.now()}] ✅ {len(stoerungen)} relevante Störungen erkannt.")
            return stoerungen

    except Exception as e:
        print(f"[{datetime.now()}] ❌ Fehler in scrape_stoerungen(): {e}")
        return []

@bot.event
async def on_ready():
    print(f"🤖 Bot läuft als {bot.user}")
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("✅ Bahn-Störungs-Bot wurde gestartet!")
    bot.loop.create_task(check_stoerungen())

async def check_stoerungen():
    global last_stoerungen, last_check_time
    await bot.wait_until_ready()
    print("🚀 check_stoerungen() gestartet")
    channel = bot.get_channel(CHANNEL_ID)

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

@bot.command()
async def status(ctx):
    if ADMIN_ID and str(ctx.author.id) != str(ADMIN_ID):
        await ctx.send("❌ Du bist nicht berechtigt.")
        return
    if last_check_time:
        await ctx.send(f"✅ Letzte Prüfung: {last_check_time.strftime('%d.%m.%Y %H:%M:%S')}")
    else:
        await ctx.send("⏳ Noch keine Prüfung erfolgt.")

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
        print("🛑 Bot manuell beendet.")
