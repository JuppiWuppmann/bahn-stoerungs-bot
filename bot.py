# BAHN-STÖRUNGS-BOT – gefixte Version mit Doppel-Klick auf "Gültigkeit von"
# Änderungen:
#  - Aggressive Overlay-Entfernung vor JEDEM Klick
#  - safe_click() mit Scroll + force=True + direktem JS-Klick
#  - Mehr Selector-Fallbacks für "Gültigkeit von"
#  - Doppelter Klick mit Pausen, um neueste Störungen zuerst zu zeigen
#  - Timeout leicht erhöht für Render-Latenz

import os
import asyncio
from datetime import datetime
from discord.ext import commands
import discord
from aiohttp import web
from playwright.async_api import async_playwright
from io import BytesIO
import traceback

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = os.getenv("ADMIN_ID")

CLICK_TIMEOUT = 20000
OVERLAY_MAX_WAIT = 25000
PAGE_LOAD_TIMEOUT = 80000

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_stoerungen = set()
last_check_time = None

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

async def safe_send_to_channel(channel, content=None, file_bytes=None, filename=None):
    if channel is None:
        print("⚠️ Channel ist None — Nachricht nicht gesendet.")
        return False
    try:
        if file_bytes:
            file_bytes.seek(0)
            await channel.send(content=content, file=discord.File(fp=file_bytes, filename=filename))
        else:
            await channel.send(content)
        return True
    except discord.Forbidden:
        print("❌ Discord Forbidden: Keine Rechte.")
        return False
    except Exception as e:
        print("❌ Fehler beim Senden:", e)
        return False

async def send_screenshot(page, fehlertext="Fehler"):
    try:
        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            print("⚠️ send_screenshot: Channel nicht gefunden.")
            return
        screenshot_bytes = await page.screenshot(type="png", full_page=True)
        buffer = BytesIO(screenshot_bytes)
        buffer.name = "screenshot.png"
        buffer.seek(0)
        await safe_send_to_channel(channel, content=f"❌ **Fehler beim Scraping:** {fehlertext}", file_bytes=buffer, filename="screenshot.png")
    except Exception as e:
        print("⚠️ Fehler beim Screenshot:", e)

async def ensure_no_overlays(page, max_wait_ms=OVERLAY_MAX_WAIT):
    print("🔍 Entferne Overlays...")
    start_ts = datetime.now().timestamp()
    while True:
        removed_any = False
        try:
            btn_selectors = [
                "button:has-text('Ablehnen')",
                "button:has-text('Alles akzeptieren')",
                "button:has-text('Alle akzeptieren')",
                "button[aria-label='Schließen']",
                "button[aria-label='Close']",
                "button:has-text('Schließen')"
            ]
            for sel in btn_selectors:
                for b in await page.query_selector_all(sel):
                    try:
                        await b.click()
                        await asyncio.sleep(0.3)
                        print(f"✅ Overlay-Button {sel} geklickt")
                        removed_any = True
                    except:
                        pass
        except:
            pass
        try:
            overlay_selectors = [
                "#usercentrics-cmp-ui",
                "div[role='dialog']",
                "div[class*='cookie']",
                "aside[id^='usercentrics']",
                "div[id*='cookie']",
                "div[class*='overlay']",
                "[style*='z-index']"
            ]
            for sel in overlay_selectors:
                for el in await page.query_selector_all(sel):
                    try:
                        await page.evaluate("(el) => { el.style.pointerEvents = 'none'; el.remove(); }", el)
                        removed_any = True
                    except:
                        pass
        except:
            pass
        if (datetime.now().timestamp() - start_ts) * 1000 > max_wait_ms:
            break
        if not removed_any:
            break
        await asyncio.sleep(0.25)

async def safe_click(page, selector, timeout_ms=CLICK_TIMEOUT, description="Element", alt_selectors=None):
    alt_selectors = alt_selectors or []
    selectors = [selector] + alt_selectors
    attempts = 4
    for attempt in range(1, attempts + 1):
        try:
            await ensure_no_overlays(page)
            for sel in selectors:
                try:
                    el = await page.wait_for_selector(sel, timeout=timeout_ms)
                    try:
                        await el.scroll_into_view_if_needed()
                    except:
                        await page.evaluate("el => el.scrollIntoView()", el)
                    try:
                        await el.click(timeout=timeout_ms)
                        print(f"✅ {description} geklickt ({sel}) Versuch {attempt}")
                        return True
                    except:
                        try:
                            await el.click(force=True)
                            print(f"✅ {description} force-geklickt ({sel}) Versuch {attempt}")
                            return True
                        except:
                            try:
                                await page.eval_on_selector(sel, "el => el.click()")
                                print(f"✅ {description} JS-geklickt ({sel}) Versuch {attempt}")
                                return True
                            except:
                                pass
                except:
                    pass
            raise Exception(f"Kein Selector klickbar für {description}")
        except Exception as e:
            print(f"⚠️ {description} Klick fehlgeschlagen Versuch {attempt}: {e}")
            if attempt == attempts - 1:
                try:
                    await page.reload()
                    await page.wait_for_load_state("networkidle")
                except:
                    pass
            if attempt == attempts:
                await send_screenshot(page, f"{description} konnte nicht geklickt werden: {e}")
                return False
            await asyncio.sleep(0.5)
    return False

async def scrape_stoerungen():
    global last_stoerungen
    print(f"[{datetime.now()}] 🔁 scrape_stoerungen gestartet")
    browser = None
    context = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1366, "height": 900})
            page = await context.new_page()
            await page.set_extra_http_headers({"Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
            await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(1)
            await ensure_no_overlays(page)

            if not await safe_click(page, "button[aria-label='Filter öffnen']", description="Filter öffnen",
                                    alt_selectors=["button[aria-label='Filter']", "button:has-text('Filter')", "text=Filter"]):
                return []

            await ensure_no_overlays(page)

            for label_text in ["Baustellen", "Streckenruhen"]:
                try:
                    label = await page.query_selector(f"label:has-text('{label_text}')")
                    if label:
                        cb = await label.query_selector("input[type='checkbox']")
                        if cb and await cb.is_checked():
                            try:
                                await cb.click()
                            except:
                                await page.eval_on_selector(f"label:has-text('{label_text}')", "el => el.click()")
                except:
                    pass

            if not await safe_click(page, "text=Einschränkungen", description="Einschränkungen aktivieren",
                                    alt_selectors=["button:has-text('Einschränkungen')", "a:has-text('Einschränkungen')"]):
                return []

            await asyncio.sleep(0.7)
            await ensure_no_overlays(page)

            # Doppel-Klick für neueste zuerst
            for i in range(2):
                if await safe_click(page, 'th:has-text("Gültigkeit von")', description=f"Tabelle sortieren Klick {i+1}",
                                    alt_selectors=["text=Gültigkeit von", "table thead th:nth-last-child(2)"]):
                    await asyncio.sleep(0.3)
                else:
                    print("⚠️ Sortierung nicht möglich.")
                    break

            await page.wait_for_selector("table tbody tr", timeout=20000)
            rows = await page.query_selector_all("table tbody tr")
            print(f"🔍 Gefundene Zeilen: {len(rows)}")

            new_stoerungen = []
            for row in rows:
                try:
                    cols = await row.query_selector_all("td")
                    if len(cols) < 8:
                        continue
                    id_text = (await cols[0].inner_text()).strip()
                    typ = (await cols[1].inner_text()).strip()
                    ort = (await cols[2].inner_text()).strip()
                    region = (await cols[3].inner_text()).strip()
                    wirkung = (await cols[4].inner_text()).strip()
                    ursache = (await cols[5].inner_text()).strip()
                    gueltig_von = (await cols[6].inner_text()).strip()
                    gueltig_bis = (await cols[7].inner_text()).strip()
                    if typ.lower() in ["baustelle", "streckenruhe"]:
                        continue
                    if id_text not in last_stoerungen:
                        new_stoerungen.append({"id": id_text, "text": f"""🚨 **Neue Bahn-Störung entdeckt!**
🆔 **ID:** {id_text}
📌 **Typ:** {typ}
📍 **Ort:** {ort}
🗺️ **Region:** {region}
🚦 **Wirkung:** {wirkung}
📋 **Ursache:** {ursache}
⏰ **Gültigkeit:** {gueltig_von} → {gueltig_bis}"""})
                except:
                    pass
            await context.close()
            await browser.close()
            return new_stoerungen
    except Exception as e:
        print("❌ Fehler beim Scraping:", e)
        traceback.print_exc()
        try:
            if context: await context.close()
            if browser: await browser.close()
        except:
            pass
        return []

async def check_stoerungen():
    global last_stoerungen, last_check_time
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await safe_send_to_channel(channel, "✅ Bahn-Störungs-Bot gestartet!")
    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()
        last_check_time = datetime.now()
        if stoerungen:
            channel = bot.get_channel(CHANNEL_ID)
            for s in stoerungen:
                if s["id"] not in last_stoerungen:
                    last_stoerungen.add(s["id"])
                    if channel:
                        await safe_send_to_channel(channel, s["text"])
        else:
            print("ℹ️ Keine neuen Störungen.")
        await asyncio.sleep(600)

@bot.command()
async def status(ctx):
    if ADMIN_ID and str(ctx.author.id) != str(ADMIN_ID):
        await ctx.send("❌ Nicht berechtigt.")
        return
    if last_check_time:
        await ctx.send(f"✅ Letzte Prüfung: {last_check_time.strftime('%d.%m.%Y %H:%M:%S')}")
    else:
        await ctx.send("⏳ Noch keine Prüfung.")

@bot.event
async def on_ready():
    print(f"🤖 Bot ready as {bot.user}")
    bot.loop.create_task(check_stoerungen())

async def main():
    await asyncio.gather(start_web_server(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot beendet.")
