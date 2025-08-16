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

last_stoerungen = {}
last_check_time = None

# ---------------------------
# Health Server
# ---------------------------
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

# ---------------------------
# Discord Hilfsfunktionen
# ---------------------------
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
        screenshot_bytes = await page.screenshot(type="png")
        buffer = BytesIO(screenshot_bytes)
        buffer.name = "screenshot.png"
        buffer.seek(0)
        await safe_send_to_channel(channel, content=f"❌ **Fehler beim Scraping:** {fehlertext}", file_bytes=buffer, filename="screenshot.png")
    except Exception as e:
        print("⚠️ Fehler beim Screenshot:", e)

# ---------------------------
# Robuste Klick-Funktion
# ---------------------------
async def safe_click(page, selector, timeout_ms=CLICK_TIMEOUT, description="Element", alt_selectors=None, log=False):
    alt_selectors = alt_selectors or []
    selectors = [selector] + alt_selectors

    for attempt in range(4):
        try:
            await ensure_no_overlays(page)

            for sel in selectors:
                try:
                    el = await page.wait_for_selector(sel, timeout=timeout_ms)
                    if el:
                        await el.scroll_into_view_if_needed()
                        if log:
                            await page.evaluate("(el) => el.style.border = '2px solid red'", el)
                        await el.click(timeout=timeout_ms)
                        if log:
                            print(f"✅ Klick erfolgreich auf: {sel}")
                        return True
                except Exception as inner_e:
                    if log:
                        print(f"⚠️ Versuch fehlgeschlagen für {sel}: {inner_e}")
                    continue

            raise Exception(f"Kein Selector klickbar für {description}")
        except Exception as e:
            if attempt == 3:
                await send_screenshot(page, f"{description} konnte nicht geklickt werden: {e}")
                return False
        await asyncio.sleep(0.5)

    return False

# ---------------------------
# Overlay-Handling
# ---------------------------
async def ensure_no_overlays(page, max_wait_ms=OVERLAY_MAX_WAIT):
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
                el = await page.query_selector(sel)
                if el:
                    try:
                        await el.click()
                        await asyncio.sleep(0.3)
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
                el = await page.query_selector(sel)
                if el:
                    try:
                        await page.evaluate("(el) => el.remove()", el)
                        removed_any = True
                    except:
                        pass
        except:
            pass
        if (datetime.now().timestamp() - start_ts) * 1000 > max_wait_ms or not removed_any:
            break
        await asyncio.sleep(0.25)

# ---------------------------
# X-Integration
# ---------------------------
async def send_to_x(message: str):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(storage_state="x_storage.json")
            page = await context.new_page()
            await page.goto("https://x.com/compose/tweet")
            await page.wait_for_selector("div[role='textbox']", timeout=15000)
            await page.fill("div[role='textbox']", message[:280])
            await page.click("div[data-testid='tweetButtonInline']")
            print("✅ Nachricht auf X gepostet:", message[:80])
            await context.close()
            await browser.close()
    except Exception as e:
        print("❌ Fehler beim Posten auf X:", e)

# ---------------------------
# Scraping
# ---------------------------
async def scrape_stoerungen():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(viewport={"width": 1024, "height": 768})
            page = await context.new_page()
            await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)
            await page.wait_for_load_state("networkidle")
            await ensure_no_overlays(page)

            if not await safe_click(
                page,
                "button[aria-label='open drawer']",
                description="Filter öffnen",
                alt_selectors=[
                    "button[aria-label='Filter öffnen']",
                    "button:has(img[alt='Filtereinstellungen'])"
                ],
                log=True
            ):
                await context.close()
                await browser.close()
                return []

            for label_text in ["Baustellen", "Streckenruhen"]:
                label = await page.query_selector(f"label:has-text('{label_text}')")
                if label:
                    cb = await label.query_selector("input[type='checkbox']")
                    if cb and await cb.is_checked():
                        await cb.click()

            if not await safe_click(page, "text=Einschränkungen", description="Einschränkungen aktivieren"):
                await context.close()
                await browser.close()
                return []

            for i in range(2):
                await safe_click(page, 'th:has-text("Gültigkeit von")', description=f"Tabelle sortieren Klick {i+1}")
                await asyncio.sleep(0.3)

            await page.wait_for_selector("table tbody tr", timeout=20000)
            rows = await page.query_selector_all("table tbody tr")
            rows = rows[:50]

            stoerungen = []
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

                    try:
                        gueltig_bis_dt = datetime.strptime(gueltig_bis, "%d.%m.%Y %H:%M")
                    except:
                        gueltig_bis_dt = None

                    stoerungen.append({
                        "id": id_text,
                        "gueltig_bis"
                        "gueltig_bis": gueltig_bis_dt,
                        "gueltig_von": gueltig_von,
                        "typ": typ,
                        "ort": ort,
                        "region": region,
                        "wirkung": wirkung,
                        "ursache": ursache,
                        "text": f"""🚨 **Neue Bahn-Störung entdeckt!**
🆔 ID: {id_text}
📌 Typ: {typ}
📍 Ort: {ort}
🗺️ Region: {region}
🚦 Wirkung: {wirkung}
📋 Ursache: {ursache}
⏰ Gültigkeit: {gueltig_von} → {gueltig_bis}"""
                    })
                except:
                    pass

            await context.close()
            await browser.close()
            return stoerungen
    except Exception as e:
        print("❌ Fehler beim Scraping:", e)
        traceback.print_exc()
        return []

# ---------------------------
# Haupt-Logik
# ---------------------------
async def check_stoerungen():
    global last_stoerungen, last_check_time
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await safe_send_to_channel(channel, "✅ Bahn-Störungs-Bot gestartet!")

    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()
        last_check_time = datetime.now()
        current_ids = {s["id"] for s in stoerungen}

        # Beendete Störungen
        for sid, details in list(last_stoerungen.items()):
            if sid not in current_ids or (details["gueltig_bis"] and details["gueltig_bis"] < datetime.now()):
                if channel:
                    msg = f"""✅ **Bahn-Störung behoben!**
🆔 ID: {sid}
📍 Ort: {details['ort']}
🚦 Wirkung: {details['wirkung']}
📋 Ursache: {details['ursache']}
⏰ Dauer: {details['gueltig_von']} → {details['gueltig_bis'].strftime('%d.%m.%Y %H:%M') if details['gueltig_bis'] else 'unbekannt'}"""
                    await safe_send_to_channel(channel, msg)
                    await send_to_x(
                        f"✅ Bahn-Störung behoben!\nID: {sid}\nOrt: {details['ort']}\nWirkung: {details['wirkung']}\nUrsache: {details['ursache']}"
                    )
                del last_stoerungen[sid]

        # Neue Störungen
        for s in stoerungen:
            if s["id"] not in last_stoerungen:
                last_stoerungen[s["id"]] = {
                    "gueltig_bis": s["gueltig_bis"],
                    "gueltig_von": s["gueltig_von"],
                    "typ": s["typ"],
                    "ort": s["ort"],
                    "region": s["region"],
                    "wirkung": s["wirkung"],
                    "ursache": s["ursache"]
                }
                if channel:
                    await safe_send_to_channel(channel, s["text"])
                    await send_to_x(
                        f"🚨 Neue Bahn-Störung!\nID: {s['id']}\nOrt: {s['ort']}\nWirkung: {s['wirkung']}\nUrsache: {s['ursache']}"
                    )

        await asyncio.sleep(600)

# ---------------------------
# Discord Commands & Events
# ---------------------------
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

# ---------------------------
# Start
# ---------------------------
async def main():
    await asyncio.gather(start_web_server(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot beendet.")

