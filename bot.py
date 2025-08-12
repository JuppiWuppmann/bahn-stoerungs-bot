import os
import asyncio
from datetime import datetime
from discord.ext import commands
import discord
from aiohttp import web
from playwright.async_api import async_playwright
from io import BytesIO
import traceback

# -------------------------
# Konfiguration / Umgebungsvariablen
# -------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = os.getenv("ADMIN_ID")

# Timeouts (ms / s)
CLICK_TIMEOUT_MS = 20000
OVERLAY_MAX_WAIT_MS = 25000
PAGE_LOAD_TIMEOUT_MS = 80000
SCRAPE_INTERVAL_SEC = int(os.getenv("SCRAPE_INTERVAL_SEC", "600"))  # Standard 600s = 10min

# -------------------------
# Discord Setup
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_stoerungen = set()
last_check_time = None

# -------------------------
# Healthcheck (für Render / Uptimerobot)
# -------------------------
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

# -------------------------
# Hilfsfunktionen für Discord Send
# -------------------------
async def safe_send_to_channel(channel, content=None, file_bytes=None, filename=None):
    """Sendet an Channel, fängt fehlende Rechte / Fehler ab."""
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
        print("❌ Discord Forbidden: Bot hat keine Rechte zum Senden in diesen Channel (403).")
        return False
    except Exception as e:
        print("❌ Fehler beim Senden an Discord:", e)
        return False

async def send_screenshot(page, fehlertext="Fehler"):
    """Erstellt Screenshot und versucht, ihn in den Bot-Channel zu senden (wenn möglich)."""
    try:
        channel = bot.get_channel(CHANNEL_ID)
        if not channel:
            print("⚠️ send_screenshot: Channel nicht gefunden.")
            return
        screenshot_bytes = await page.screenshot(type="png", full_page=False)
        buffer = BytesIO(screenshot_bytes)
        buffer.name = "screenshot.png"
        buffer.seek(0)
        await safe_send_to_channel(channel, content=f"❌ **Fehler beim Scraping:** {fehlertext}", file_bytes=buffer, filename="screenshot.png")
    except Exception as e:
        print("⚠️ Fehler beim Erstellen/Senden des Screenshots:", e)

# -------------------------
# Overlay-Handling (aggressiv & mehrere Strategien)
# -------------------------
async def ensure_no_overlays(page, max_wait_ms=OVERLAY_MAX_WAIT_MS):
    """
    Versucht in einer Schleife störende Overlays zu entfernen:
     - klickt Ablehnen / Akzeptieren / Schließen Buttons
     - entfernt per JS bekannte Overlay-Elemente
     - setzt pointer-events:none bevor entfernt wird
    """
    start_ts = datetime.now().timestamp()
    while True:
        removed_any = False
        try:
            # Buttons klicken
            btn_selectors = [
                "button:has-text('Ablehnen')",
                "button:has-text('Alles akzeptieren')",
                "button:has-text('Alle akzeptieren')",
                "button[aria-label='Schließen']",
                "button[aria-label='Close']",
                "button:has-text('Schließen')",
                "button:has-text('Accept')",
            ]
            for sel in btn_selectors:
                try:
                    btns = await page.query_selector_all(sel)
                    for b in btns:
                        try:
                            await b.click()
                            await asyncio.sleep(0.35)
                            print(f"✅ Overlay-Button {sel} geklickt")
                            removed_any = True
                        except Exception:
                            # ignore single button click failure
                            pass
                except Exception:
                    pass
        except Exception as e:
            print("⚠️ Fehler beim Klick auf Overlay-Buttons:", e)

        # Spezifische Overlay-IDs / generische Elemente per JS entfernen
        try:
            overlay_selectors = [
                "#usercentrics-cmp-ui",
                "aside[id^='usercentrics']",
                "div[role='dialog']",
                "div[class*='cookie']",
                "div[id*='cookie']",
                "div[class*='overlay']",
                "[style*='z-index']"
            ]
            for sel in overlay_selectors:
                try:
                    els = await page.query_selector_all(sel)
                    for el in els:
                        try:
                            # set pointer-events none then remove
                            await page.evaluate("(e)=>{ e.style.pointerEvents='none'; e.remove(); }", el)
                            print(f"🗑️ Overlay entfernt (selector={sel})")
                            removed_any = True
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception as e:
            print("⚠️ Fehler beim Entfernen generischer Overlays:", e)

        # stop if timed out
        if (datetime.now().timestamp() - start_ts) * 1000 > max_wait_ms:
            print("⚠️ Overlay-Entfernung abgebrochen (Zeitlimit erreicht)")
            break

        if not removed_any:
            # nothing removed this pass -> done
            break

        # small pause before next loop
        await asyncio.sleep(0.2)

# -------------------------
# safe_click mit mehreren Fallbacks (click, force click, js-click, reload)
# -------------------------
async def safe_click(page, selector, timeout_ms=CLICK_TIMEOUT_MS, description="Element", alt_selectors=None):
    """
    Versucht mehrfach den angegebenen selector zu klicken.
    - löscht Overlays vor jedem Versuch
    - probiert alternative selector strings
    - versucht normal click, force click, js click
    - reload vor letzten Versuch
    """
    alt_selectors = alt_selectors or []
    selectors = [selector] + alt_selectors
    attempts = 4
    for attempt in range(1, attempts + 1):
        try:
            await ensure_no_overlays(page)
            for sel in selectors:
                try:
                    el = await page.wait_for_selector(sel, timeout=timeout_ms)
                    # bring into view
                    try:
                        await el.scroll_into_view_if_needed()
                    except Exception:
                        try:
                            await page.evaluate("(e)=>e.scrollIntoView()", el)
                        except Exception:
                            pass
                    # try normal click
                    try:
                        await el.click(timeout=timeout_ms)
                        await asyncio.sleep(0.25)
                        print(f"✅ {description} geklickt mit '{sel}' (Versuch {attempt})")
                        return True
                    except Exception:
                        # try force click
                        try:
                            await el.click(force=True)
                            await asyncio.sleep(0.25)
                            print(f"✅ {description} force-geklickt mit '{sel}' (Versuch {attempt})")
                            return True
                        except Exception:
                            # try JS click
                            try:
                                await page.eval_on_selector(sel, "el => el.click()")
                                await asyncio.sleep(0.25)
                                print(f"✅ {description} JS-geklickt mit '{sel}' (Versuch {attempt})")
                                return True
                            except Exception as e_js:
                                print(f"⚠️ JS-click für '{sel}' gescheitert: {e_js}")
                                # fallthrough -> try next sel
                except Exception as e_sel:
                    # selector not found/visible - ignore here, try next selector
                    # print(f"selector {sel} not found/ready: {e_sel}")
                    pass
            raise Exception(f"Kein Selector klickbar für {description} (Versuch {attempt})")
        except Exception as e:
            print(f"⚠️ {description} Klick fehlgeschlagen (Versuch {attempt}): {e}")
            # Reload bevor letzter Versuch
            if attempt == attempts - 1:
                try:
                    print("🔄 Seite reload vor letztem Versuch...")
                    await page.reload()
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(0.6)
                except Exception as re:
                    print("⚠️ Reload fehlgeschlagen:", re)
            if attempt == attempts:
                try:
                    await send_screenshot(page, f"{description} konnte nicht geklickt werden: {e}")
                except Exception:
                    pass
                return False
            await asyncio.sleep(0.5)
    return False

# -------------------------
# Scraping-Funktion (komplett robust)
# -------------------------
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

            # 1) open page
            await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT_MS)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(0.8)

            # 2) remove overlays aggressively
            await ensure_no_overlays(page)

            # 3) Filter öffnen (robust, mehrere selector-fallbacks)
            ok = await safe_click(
                page,
                "button[aria-label='Filter öffnen']",
                description="Filter öffnen",
                alt_selectors=[
                    "button[aria-label='Filter']",
                    "button:has-text('Filter')",
                    "text=Filter"
                ]
            )
            if not ok:
                print("❌ Filter konnte nicht geöffnet werden -> Abbruch dieses Laufs")
                await context.close()
                await browser.close()
                return []

            # 4) overlay-check again (some overlays can appear after clicking)
            await ensure_no_overlays(page)

            # 5) Baustellen & Streckenruhen deaktivieren (falls sichtbar)
            for label_text in ["Baustellen", "Streckenruhen"]:
                try:
                    label = await page.query_selector(f"label:has-text('{label_text}')")
                    if label:
                        cb = await label.query_selector("input[type='checkbox']")
                        if cb:
                            try:
                                is_checked = await cb.is_checked()
                            except Exception:
                                is_checked = False
                            if is_checked:
                                try:
                                    await cb.click()
                                    await asyncio.sleep(0.3)
                                    print(f"✅ '{label_text}' deaktiviert (checkbox click)")
                                except Exception:
                                    # fallback: click label itself
                                    try:
                                        await page.eval_on_selector(f"label:has-text('{label_text}')", "el => el.click()")
                                        await asyncio.sleep(0.3)
                                        print(f"✅ '{label_text}' deaktiviert (label-click fallback)")
                                    except Exception:
                                        print(f"⚠️ Konnte '{label_text}' nicht deaktivieren")
                except Exception as e:
                    print(f"⚠️ Fehler beim Deaktivieren von {label_text}: {e}")

            # 6) 'Einschränkungen' Tab / Button aktivieren
            ok = await safe_click(
                page,
                "text=Einschränkungen",
                description="Einschränkungen aktivieren",
                alt_selectors=["button:has-text('Einschränkungen')", "a:has-text('Einschränkungen')"]
            )
            if not ok:
                print("❌ 'Einschränkungen' Tab konnte nicht aktiviert werden -> Abbruch dieses Laufs")
                await context.close()
                await browser.close()
                return []

            await asyncio.sleep(0.6)
            await ensure_no_overlays(page)

            # 7) Sortieren nach "Gültigkeit von" (2x click). Verwendet Fallbacks.
            ok = await safe_click(
                page,
                "th:has-text('Gültigkeit von')",
                description="Tabelle sortieren",
                alt_selectors=["text=Gültigkeit von", "table thead th:nth-last-child(2)"]
            )
            if ok:
                # second click attempt if available
                try:
                    await asyncio.sleep(0.35)
                    # try js click to ensure second toggle
                    try:
                        await page.eval_on_selector("th:has-text('Gültigkeit von')", "el => el.click()")
                    except Exception:
                        # fallback: click same selector normally again via safe_click small timeout
                        await safe_click(page, "th:has-text('Gültigkeit von')", timeout_ms=5000, description="Tabelle sortieren (zweites Mal)")
                except Exception:
                    pass
            else:
                print("⚠️ Sortierung nicht möglich - wir fahren mit aktueller Reihenfolge fort")

            # 8) Tabelle auslesen
            await page.wait_for_selector("table tbody tr", timeout=20000)
            rows = await page.query_selector_all("table tbody tr")
            print(f"🔍 Gefundene Tabellenzeilen: {len(rows)}")

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
                        text = (
                            "🚨 **Neue Bahn-Störung entdeckt!**\n\n"
                            f"🆔 **ID:** {id_text}\n"
                            f"📌 **Typ:** {typ}\n"
                            f"📍 **Ort:** {ort}\n"
                            f"🗺️ **Region:** {region}\n"
                            f"🚦 **Wirkung:** {wirkung}\n"
                            f"📋 **Ursache:** {ursache}\n"
                            f"⏰ **Gültigkeit:** {gueltig_von} → {gueltig_bis}"
                        )
                        new_stoerungen.append({"id": id_text, "text": text})
                except Exception as e:
                    print("⚠️ Fehler beim Auslesen einer Tabellenzeile:", e)
                    continue

            # cleanup
            await context.close()
            await browser.close()
            print(f"🔍 Neue Störungen erkannt: {len(new_stoerungen)}")
            return new_stoerungen

    except Exception as e:
        print("❌ Unerwarteter Fehler beim Scraping:", e)
        traceback.print_exc()
        try:
            if context:
                await context.close()
            if browser:
                await browser.close()
        except Exception:
            pass
        return []

# -------------------------
# Prüf-Loop: Scrapen + Discord
# -------------------------
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
                        print(f"⚠️ Channel nicht gefunden - Störung {s['id']} nicht gesendet")
        else:
            print("ℹ️ Keine neuen Störungen in diesem Durchlauf")
        await asyncio.sleep(SCRAPE_INTERVAL_SEC)

# -------------------------
# Status Command
# -------------------------
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
    print(f"🤖 Bot ready as {bot.user}")
    bot.loop.create_task(check_stoerungen())

# -------------------------
# Main
# -------------------------
async def main():
    await asyncio.gather(start_web_server(), bot.start(DISCORD_TOKEN))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Bot beendet.")

