# bot.py  —  kompakt + X-Posting + wenig RAM
import os, asyncio, json, traceback
from datetime import datetime
import discord
from discord.ext import commands
from aiohttp import web
from playwright.async_api import async_playwright

# ---------- ENV ----------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID    = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID      = os.getenv("ADMIN_ID")
POST_TO_X     = os.getenv("POST_TO_X", "0") == "1"
X_USERNAME    = os.getenv("X_USERNAME")
X_PASSWORD    = os.getenv("X_PASSWORD")
X_STORAGE     = os.getenv("X_STORAGE", "x_storage.json")

# ---------- Playwright / Scrape ----------
PAGE_LOAD_TIMEOUT = 80000
CLICK_TIMEOUT     = 20000

# Discord Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# State
last_stoerungen = {}
last_check_time = None

# Globale Browser-Objekte (einmal starten, wiederverwenden → spart RAM)
_pw = None
_browser = None
_x_context = None   # persistenter X-Context (dauerhaft eingeloggt)

# ---------------- Healthcheck (Render) ----------------
async def handle_health(_):
    return web.Response(text="OK")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health)
    app.router.add_get("/health", handle_health)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🌐 Health-Webserver läuft auf Port {port}")

# ---------------- X: Login + Post ----------------
async def ensure_playwright_and_browser():
    global _pw, _browser
    if _browser:
        return
    _pw = await async_playwright().start()
    # Images/GPU aus → etwas weniger RAM/Traffic
    _browser = await _pw.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-gpu", "--blink-settings=imagesEnabled=false"
        ]
    )

async def init_x_context():
    """Dauerhaft eingeloggt über storage_state. Falls Storage fehlt: Login und speichern."""
    if not POST_TO_X:
        return
    global _x_context
    await ensure_playwright_and_browser()
    try:
        if os.path.exists(X_STORAGE):
            _x_context = await _browser.new_context(storage_state=X_STORAGE)
        else:
            _x_context = await _browser.new_context()
            page = await _x_context.new_page()
            await page.goto("https://x.com/login", timeout=60000)
            await page.fill('input[name="text"]', X_USERNAME)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(1200)
            await page.fill('input[name="password"]', X_PASSWORD)
            await page.keyboard.press("Enter")
            # warten bis eingeloggt (Nav sichtbar)
            try:
                await page.wait_for_selector("nav", timeout=20000)
            except:
                # Fallback: Home versuchen
                await page.goto("https://x.com/home", timeout=60000)
                await page.wait_for_selector("nav", timeout=20000)
            await _x_context.storage_state(path=X_STORAGE)
            await page.close()
        print("✅ X: Session bereit (persistent).")
    except Exception as e:
        print("⚠️ X-Login/Storage fehlgeschlagen:", e)

def _chunk_for_x(text, limit=280):
    """Einfacher Split in <=280-Char-Teile (für Threads)."""
    parts, cur = [], ""
    for token in text.split():
        if len(cur) + 1 + len(token) > limit:
            if cur:
                parts.append(cur.strip())
            cur = token
        else:
            cur = (cur + " " + token).strip()
    if cur:
        parts.append(cur.strip())
    return parts or [""]

async def post_to_x_minimal(message: str):
    """Postet NUR den übergebenen Text (ID/Ort/Wirkung/Ursache). Keine Extra-Texte."""
    if not POST_TO_X:
        return
    if not _x_context:
        await init_x_context()
        if not _x_context:
            return
    try:
        page = await _x_context.new_page()
        await page.goto("https://x.com/compose/tweet", timeout=60000)
        # robuste Auswahl der Textbox
        selectors = [
            'div[role="textbox"]',
            'div[data-testid="tweetTextarea_0"]',
            'div[aria-label="Tweet text"]',
        ]
        tb = None
        for sel in selectors:
            try:
                tb = await page.wait_for_selector(sel, timeout=8000)
                if tb:
                    break
            except:
                pass
        if not tb:
            await page.close()
            return
        chunks = _chunk_for_x(message, 280)
        # Erster Chunk
        await tb.click()
        await page.keyboard.type(chunks[0])
        # Weitere Chunks als Thread
        for extra in chunks[1:]:
            try:
                add_btn = await page.wait_for_selector('div[data-testid="addButton"]', timeout=4000)
                await add_btn.click()
            except:
                pass
            await page.keyboard.type("\n\n" + extra)
        # Tweet senden
        for btn in ['div[data-testid="tweetButton"]', 'div[data-testid="tweetButtonInline"]']:
            try:
                b = await page.wait_for_selector(btn, timeout=5000)
                await b.click()
                break
            except:
                continue
        await page.wait_for_timeout(1200)
        await page.close()
    except Exception as e:
        print("❌ Fehler beim Posten auf X:", e)

def build_x_text(item):
    # EXACT: nur id, ort, wirkung, ursache (ohne zusätzliche Wörter/Emojis)
    return f"ID: {item['id']}\nOrt: {item['ort']}\nWirkung: {item['wirkung']}\nUrsache: {item['ursache']}"

# ---------------- Scraper ----------------
async def scrape_stoerungen():
    """Liest die Tabelle, filtert Baustellen/Streckenruhe raus. Schlank & robust."""
    await ensure_playwright_and_browser()
    context = await _browser.new_context(  # frischer Context je Lauf → vermeidet Leaks
        viewport={"width": 1280, "height": 800},
        java_script_enabled=True,
    )
    page = await context.new_page()
    stoerungen = []
    try:
        await page.set_extra_http_headers({"Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
        await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)
        # evtl. Cookies/Overlays schließen
        for sel in [
            "button:has-text('Ablehnen')",
            "button:has-text('Alle akzeptieren')",
            "button:has-text('Alles akzeptieren')",
            "button[aria-label='Schließen']",
            "button[aria-label='Close']",
            "button:has-text('Schließen')",
        ]:
            try:
                btn = await page.query_selector(sel)
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(200)
            except:
                pass

        # Filter öffnen (robust, ohne Abbruch)
        try:
            # mehrere Versuche / Fallbacks
            try:
                await page.get_by_role("button", name="Filter").click(timeout=10000)
            except:
                try:
                    await page.click("button[aria-label='Filter öffnen']", timeout=10000)
                except:
                    await page.click("button:has-text('Filter')", timeout=10000)
        except:
            print("ℹ️ Filter-Button nicht gefunden – weitermachen ohne explizites Öffnen")

        # "Einschränkungen" aktivieren (wenn vorhanden)
        try:
            await page.get_by_text("Einschränkungen").click(timeout=8000)
        except:
            try:
                await page.click("text=Einschränkungen", timeout=8000)
            except:
                pass

        # Sortieren nach "Gültigkeit von" (2x)
        for _ in range(2):
            try:
                await page.click('th:has-text("Gültigkeit von")', timeout=6000)
                await page.wait_for_timeout(200)
            except:
                break

        await page.wait_for_selector("table tbody tr", timeout=20000)
        rows = await page.query_selector_all("table tbody tr")

        for row in rows:
            try:
                cols = await row.query_selector_all("td")
                if len(cols) < 8:
                    continue
                id_text     = (await cols[0].inner_text()).strip()
                typ         = (await cols[1].inner_text()).strip()
                ort         = (await cols[2].inner_text()).strip()
                region      = (await cols[3].inner_text()).strip()
                wirkung     = (await cols[4].inner_text()).strip()
                ursache     = (await cols[5].inner_text()).strip()
                gueltig_von = (await cols[6].inner_text()).strip()
                gueltig_bis = (await cols[7].inner_text()).strip()

                if typ.lower() in ("baustelle", "streckenruhe"):
                    continue

                try:
                    gb_dt = datetime.strptime(gueltig_bis, "%d.%m.%Y %H:%M")
                except:
                    gb_dt = None

                stoerungen.append({
                    "id": id_text,
                    "typ": typ,
                    "ort": ort,
                    "region": region,
                    "wirkung": wirkung,
                    "ursache": ursache,
                    "gueltig_von": gueltig_von,
                    "gueltig_bis": gb_dt,
                    "discord_text": (
                        f"🚨 **Neue Bahn-Störung entdeckt!**\n"
                        f"🆔 **ID:** {id_text}\n"
                        f"📌 **Typ:** {typ}\n"
                        f"📍 **Ort:** {ort}\n"
                        f"🗺️ **Region:** {region}\n"
                        f"🚦 **Wirkung:** {wirkung}\n"
                        f"📋 **Ursache:** {ursache}\n"
                        f"⏰ **Gültigkeit:** {gueltig_von} → {gueltig_bis}"
                    ),
                })
            except:
                continue
    except Exception as e:
        print("❌ Fehler beim Scraping:", e)
        traceback.print_exc()
    finally:
        try:
            await page.close()
        except:
            pass
        await context.close()

    return stoerungen

# ---------------- Notify-Loop ----------------
async def safe_send_to_channel(channel, content):
    if not channel:
        return
    try:
        await channel.send(content)
    except Exception as e:
        print("❌ Discord-Sendefehler:", e)

async def check_stoerungen():
    global last_stoerungen, last_check_time
    # WICHTIG: Keine Startnachricht an Discord senden (explizit gewünscht)

    while not bot.is_closed():
        try:
            stoerungen = await scrape_stoerungen()
            last_check_time = datetime.now()
            current_ids = {s["id"] for s in stoerungen}
            channel = bot.get_channel(CHANNEL_ID) if CHANNEL_ID else None

            # Beendete Störungen
            for sid, d in list(last_stoerungen.items()):
                ended = sid not in current_ids or (d["gueltig_bis"] and d["gueltig_bis"] < datetime.now())
                if ended:
                    # Discord: ausführlich
                    if channel:
                        bis_txt = d["gueltig_bis"].strftime('%d.%m.%Y %H:%M') if d["gueltig_bis"] else 'unbekannt'
                        msg = (
                            "✅ **Bahn-Störung behoben!**\n"
                            f"🆔 **ID:** {sid}\n"
                            f"📌 **Typ:** {d['typ']}\n"
                            f"📍 **Ort:** {d['ort']}\n"
                            f"🗺️ **Region:** {d['region']}\n"
                            f"🚦 **Wirkung:** {d['wirkung']}\n"
                            f"📋 **Ursache:** {d['ursache']}\n"
                            f"⏰ **Dauer:** {d['gueltig_von']} → {bis_txt}"
                        )
                        await safe_send_to_channel(channel, msg)
                    # X: NUR id/ort/wirkung/ursache
                    await post_to_x_minimal(build_x_text({
                        "id": sid, "ort": d["ort"], "wirkung": d["wirkung"], "ursache": d["ursache"]
                    }))
                    del last_stoerungen[sid]

            # Neue Störungen
            for s in stoerungen:
                if s["id"] not in last_stoerungen:
                    last_stoerungen[s["id"]] = {
                        "typ": s["typ"],
                        "ort": s["ort"],
                        "region": s["region"],
                        "wirkung": s["wirkung"],
                        "ursache": s["ursache"],
                        "gueltig_von": s["gueltig_von"],
                        "gueltig_bis": s["gueltig_bis"],
                    }
                    # Discord: ausführlich
                    if channel:
                        await safe_send_to_channel(channel, s["discord_text"])
                    # X: NUR id/ort/wirkung/ursache
                    await post_to_x_minimal(build_x_text(s))
        except Exception as e:
            print("⚠️ Loop-Fehler:", e)
            traceback.print_exc()

        # 10 Minuten schlafen (Render-RAM schonen; kannst du anpassen)
        await asyncio.sleep(600)

# ---------------- Commands / Events ----------------
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
    # X-Context vorbereiten (asynchron, stört Discord nicht)
    if POST_TO_X:
        await init_x_context()
    # Check-Loop starten
    bot.loop.create_task(check_stoerungen())

# ---------------- Main ----------------
async def main():
    await asyncio.gather(start_web_server(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot beendet.")
    finally:
        # Browser ordentlich schließen (RAM freigeben)
        try:
            if _x_context:
                asyncio.get_event_loop().run_until_complete(_x_context.close())
        except:
            pass

