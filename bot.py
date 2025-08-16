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

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_stoerungen = {}
last_check_time = None

# ---------- X (Twitter) Poster ----------
async def post_to_x(message: str):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(storage_state="x_storage.json")
            page = await context.new_page()

            await page.goto("https://x.com/compose/tweet")
            await page.fill("div[aria-label='Posten Text eingeben']", message[:270])  # max Länge
            await page.click("div[data-testid='tweetButtonInline']")
            print("🐦 X-Post gesendet!")

            await context.storage_state(path="x_storage.json")
            await context.close()
            await browser.close()
    except Exception as e:
        print("❌ Fehler beim X-Post:", e)


# ---------- Discord Helper ----------
async def safe_send_to_channel(channel, content=None):
    if channel is None:
        return False
    try:
        await channel.send(content)
        return True
    except Exception as e:
        print("❌ Fehler beim Senden an Discord:", e)
        return False


# ---------- Webserver für Healthcheck ----------
async def handle_health(request):
    return web.Response(text="OK")

async def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    app = web.Application()
    app.router.add_get("/", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Health-Webserver läuft auf Port {port}")


# ---------- Scraper ----------
async def scrape_stoerungen():
    browser = None
    context = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = await browser.new_context(viewport={"width": 1366, "height": 900})
            page = await context.new_page()
            await page.goto("https://strecken-info.de/", timeout=80000)
            await page.wait_for_load_state("networkidle")

            # Filter öffnen
            await page.click("button[aria-label='Filter öffnen']", timeout=15000)

            # Baustellen und Streckenruhen abwählen
            for label_text in ["Baustellen", "Streckenruhen"]:
                try:
                    label = await page.query_selector(f"label:has-text('{label_text}')")
                    if label:
                        cb = await label.query_selector("input[type='checkbox']")
                        if cb and await cb.is_checked():
                            await cb.click()
                except:
                    pass

            # Einschränkungen aktivieren
            await page.click("text=Einschränkungen")

            # Tabelle sortieren
            await page.click('th:has-text("Gültigkeit von")')
            await asyncio.sleep(0.5)

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
                        "typ": typ,
                        "ort": ort,
                        "wirkung": wirkung,
                        "ursache": ursache,
                        "gueltig_von": gueltig_von,
                        "gueltig_bis": gueltig_bis_dt,
                        "text": f"""🚨 **Neue Bahn-Störung!**
📌 Typ: {typ}
📍 Ort: {ort}
🚦 Wirkung: {wirkung}
📋 Ursache: {ursache}
⏰ Zeitraum: {gueltig_von} → {gueltig_bis}"""
                    })
                except:
                    pass

            await context.close()
            await browser.close()
            return stoerungen

    except Exception as e:
        print("❌ Fehler beim Scraping:", e)
        traceback.print_exc()
        try:
            if context: await context.close()
            if browser: await browser.close()
        except:
            pass
        return []


# ---------- Checker ----------
async def check_stoerungen():
    global last_stoerungen, last_check_time
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)

    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()
        last_check_time = datetime.now()
        current_ids = {s["id"] for s in stoerungen}

        # Beendete
        for sid, details in list(last_stoerungen.items()):
            if sid not in current_ids or (details["gueltig_bis"] and details["gueltig_bis"] < datetime.now()):
                msg = f"""✅ Bahn-Störung behoben!
📍 Ort: {details['ort']}
🚦 Wirkung: {details['wirkung']}
📋 Ursache: {details['ursache']}
⏰ Zeitraum: {details['gueltig_von']} → {details['gueltig_bis'].strftime('%d.%m.%Y %H:%M') if details['gueltig_bis'] else 'unbekannt'}"""
                if channel: await safe_send_to_channel(channel, msg)
                await post_to_x(msg)
                del last_stoerungen[sid]

        # Neue
        for s in stoerungen:
            if s["id"] not in last_stoerungen:
                last_stoerungen[s["id"]] = s
                if channel: await safe_send_to_channel(channel, s["text"])
                await post_to_x(f"🚨 Bahn-Störung: Ort {s['ort']} – {s['wirkung']} (Ursache: {s['ursache']})")

        await asyncio.sleep(600)


# ---------- Discord Commands ----------
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


# ---------- Main ----------
async def main():
    await asyncio.gather(start_web_server(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot beendet.")
