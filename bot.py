# BAHN-STÖRUNGS-BOT – Discord + X
# - Discord: volle Infos
# - X: nur ID, Ort, Wirkung, Ursache (bei neuer und beendeter Störung)

import os
import asyncio
from datetime import datetime
from discord.ext import commands
import discord
from aiohttp import web
from playwright.async_api import async_playwright
from io import BytesIO
import traceback
from x_poster import post_to_x   # <-- Import für X-Posts

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

# ---- Healthcheck Webserver ----
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

# ---- Discord Helper ----
async def safe_send_to_channel(channel, content=None, file_bytes=None, filename=None):
    if channel is None:
        return False
    try:
        if file_bytes:
            file_bytes.seek(0)
            await channel.send(content=content, file=discord.File(fp=file_bytes, filename=filename))
        else:
            await channel.send(content)
        return True
    except:
        return False

async def send_screenshot(page, fehlertext="Fehler"):
    try:
        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            return
        screenshot_bytes = await page.screenshot(type="png", full_page=True)
        buffer = BytesIO(screenshot_bytes)
        buffer.name = "screenshot.png"
        buffer.seek(0)
        await safe_send_to_channel(channel, content=f"❌ Fehler beim Scraping: {fehlertext}", file_bytes=buffer, filename="screenshot.png")
    except:
        pass

# ---- Overlay-Handling ----
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
                for b in await page.query_selector_all(sel):
                    try:
                        await b.click()
                        await asyncio.sleep(0.3)
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
    for attempt in range(1, 4):
        try:
            await ensure_no_overlays(page)
            for sel in selectors:
                try:
                    el = await page.wait_for_selector(sel, timeout=timeout_ms)
                    await el.click()
                    return True
                except:
                    pass
        except:
            pass
        await asyncio.sleep(0.5)
    return False

# ---- Scraping ----
async def scrape_stoerungen():
    browser = None
    context = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(viewport={"width": 1366, "height": 900})
            page = await context.new_page()
            await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(1)
            await ensure_no_overlays(page)

            if not await safe_click(page, "button[aria-label='Filter öffnen']", description="Filter öffnen",
                                    alt_selectors=["button[aria-label='Filter']", "text=Filter"]):
                return []

            for label_text in ["Baustellen", "Streckenruhen"]:
                try:
                    label = await page.query_selector(f"label:has-text('{label_text}')")
                    if label:
                        cb = await label.query_selector("input[type='checkbox']")
                        if cb and await cb.is_checked():
                            await cb.click()
                except:
                    pass

            if not await safe_click(page, "text=Einschränkungen", description="Einschränkungen aktivieren"):
                return []

            await asyncio.sleep(0.7)

            rows = await page.query_selector_all("table tbody tr")
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

                    stoerungen.append({
                        "id": id_text,
                        "gueltig_bis": gueltig_bis,
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
        traceback.print_exc()
        try:
            if context: await context.close()
            if browser: await browser.close()
        except:
            pass
        return []

# ---- Haupt-Loop ----
async def check_stoerungen():
    global last_stoerungen, last_check_time
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)

    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()
        last_check_time = datetime.now()
        current_ids = {s["id"] for s in stoerungen}

        # Beendete Störungen
        for sid, details in list(last_stoerungen.items()):
            if sid not in current_ids:
                if channel:
                    await safe_send_to_channel(channel, f"""✅ **Bahn-Störung behoben!**
🆔 ID: {sid}
📌 Typ: {details['typ']}
📍 Ort: {details['ort']}
🗺️ Region: {details['region']}
🚦 Wirkung: {details['wirkung']}
📋 Ursache: {details['ursache']}
⏰ Dauer: {details['gueltig_von']} → {details['gueltig_bis']}""")
                # Kurzmeldung für X
                try:
                    msg = f"✅ Störung behoben\nID: {sid}\nOrt: {details['ort']}\nWirkung: {details['wirkung']}\nUrsache: {details['ursache']}"
                    await post_to_x(msg)
                except Exception as e:
                    print("❌ Fehler beim X-Post (behoben):", e)
                del last_stoerungen[sid]

        # Neue Störungen
        for s in stoerungen:
            if s["id"] not in last_stoerungen:
                last_stoerungen[s["id"]] = s
                if channel:
                    await safe_send_to_channel(channel, s["text"])
                # Kurzmeldung für X
                try:
                    msg = f"🚨 Bahn-Störung\nID: {s['id']}\nOrt: {s['ort']}\nWirkung: {s['wirkung']}\nUrsache: {s['ursache']}"
                    await post_to_x(msg)
                except Exception as e:
                    print("❌ Fehler beim X-Post (neu):", e)

        await asyncio.sleep(600)

# ---- Status Command ----
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

# ---- Start ----
async def main():
    await asyncio.gather(start_web_server(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot beendet.")
