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

# zeitlimits (ms / s)
CLICK_TIMEOUT = 15000      # ms für wait_for_selector in safe_click
OVERLAY_MAX_WAIT = 20000   # ms für overlay removal loop
PAGE_LOAD_TIMEOUT = 60000  # ms

# -------------------------
# Discord Bot Setup
# -------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_stoerungen = set()
last_check_time = None

# -------------------------
# Healthcheck-Webserver (für Render)
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
# Hilfsfunktionen
# -------------------------
async def safe_send_to_channel(channel, content=None, file_bytes=None, filename=None):
    """
    Sendet sicher an Discord-Channel, fängt Permission/HTTP-Fehler ab und loggt sie.
    file_bytes = BytesIO instance or None
    """
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
    """
    Screenshot an Channel schicken (wenn möglich). Fehler werden geloggt.
    """
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
        print("⚠️ Fehler beim Erstellen/Senden des Screenshots:", e)

# -------------------------
# Overlay-Handling (robust)
# -------------------------
async def ensure_no_overlays(page, max_wait_ms=OVERLAY_MAX_WAIT):
    """
    Entfernt oder deaktiviert störende Overlays wie Usercentrics, Cookie-Banner, Dialoge.
    Versucht Buttons zu klicken; falls das nicht geht, entfernt Elemente per JS.
    """
    print("🔍 Starte Overlay-Entfernung...")
    start_ts = datetime.now().timestamp()
    while True:
        removed_any = False

        try:
            # 1) direkte Buttons versuchen (Ablehnen / Alle akzeptieren / Schließen)
            btn_selectors = [
                "button:has-text('Ablehnen')",
                "button:has-text('Alles akzeptieren')",
                "button:has-text('Alle akzeptieren')",
                "button[aria-label='Schließen']",
                "button[aria-label='Close']",
                "button:has-text('Schließen')"
            ]
            for sel in btn_selectors:
                btns = await page.query_selector_all(sel)
                for b in btns:
                    try:
                        await b.click()
                        await asyncio.sleep(0.4)
                        print(f"✅ Overlay-Button {sel} geklickt")
                        removed_any = True
                    except Exception:
                        # ignore individual button click failures
                        pass
        except Exception as e:
            print("⚠️ Fehler beim Klick auf Overlay-Buttons:", e)

        try:
            # 2) gezielte Overlay-IDs / roles entfernen (Usercentrics etc.)
            overlay_selectors = [
                "#usercentrics-cmp-ui",
                "div[role='dialog']",
                "div[class*='cookie']",
                "aside[id^='usercentrics']",
                "div[id*='cookie']",
                "div[class*='overlay']",
            ]
            for sel in overlay_selectors:
                els = await page.query_selector_all(sel)
                for el in els:
                    try:
                        # Versuche: set pointer-events none, then remove
                        await page.evaluate("(el) => { el.style.pointerEvents = 'none'; el.remove(); }", el)
                        print(f"🗑️ Overlay entfernt (selector={sel})")
                        removed_any = True
                    except Exception:
                        pass
        except Exception as e:
            print("⚠️ Fehler beim Entfernen generischer Overlays:", e)

        # Abbruch wenn zeitlimit
        if (datetime.now().timestamp() - start_ts) * 1000 > max_wait_ms:
            print("⚠️ Overlay-Entfernung abgebrochen (Zeitlimit erreicht)")
            break

        if not removed_any:
            print("ℹ️ Keine Overlays mehr erkannt")
            break

        # kurze Pause bevor erneut prüfen
        await asyncio.sleep(0.25)

# -------------------------
# Sicherer Klick mit Fallbacks
# -------------------------
async def safe_click(page, selector, timeout_ms=CLICK_TIMEOUT, description="Element", alt_selectors=None):
    """
    Versucht mehrfach, ein Element zu klicken:
     - overlay removal vor jedem Versuch
     - try normal click
     - try eval_on_selector (JS click)
     - reload Seite einmal vor letztem Versuch
    """
    alt_selectors = alt_selectors or []
    selectors = [selector] + alt_selectors
    attempts = 4
    for attempt in range(1, attempts + 1):
        try:
            # erst Overlays entfernen
            await ensure_no_overlays(page)
            # probiere alle selector-Varianten
            for sel in selectors:
                try:
                    el = await page.wait_for_selector(sel, timeout=timeout_ms)
                    # 1) normaler click
                    try:
                        await el.click(timeout=timeout_ms)
                        await asyncio.sleep(0.3)
                        print(f"✅ {description} geklickt mit '{sel}' (Versuch {attempt})")
                        return True
                    except Exception as e_click:
                        # 2) fallback: klick per JS direkt in der Seite
                        try:
                            await page.eval_on_selector(sel, "el => el.click()")
                            await asyncio.sleep(0.3)
                            print(f"✅ {description} per JS click ausgeführt mit '{sel}' (Versuch {attempt})")
                            return True
                        except Exception as e_js:
                            print(f"⚠️ Klick via JS für {sel} gescheitert: {e_js}")
                            # weiter zu next sel
                except Exception as e_sel:
                    # sel nicht gefunden in diesem Versuch
                    # print(f"⚠️ Selector '{sel}' nicht gefunden: {e_sel}")
                    pass

            # wenn hier, alle selector-varianten für diesen attempt fehlgeschlagen
            raise Exception(f"Alle Selektoren für {description} gebrochen (Versuch {attempt})")
        except Exception as e:
            print(f"⚠️ {description} Klick fehlgeschlagen (Versuch {attempt}): {e}")
            # letzter Versuch: reload wenn noch nicht schon gemacht
            if attempt == attempts - 1:
                try:
                    print("🔄 Seite reload vor letztem Versuch...")
                    await page.reload()
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(0.8)
                except Exception as e_reload:
                    print("⚠️ Reload fehlgeschlagen:", e_reload)
            if attempt == attempts:
                # sende screenshot + abbrechen
                try:
                    await send_screenshot(page, f"{description} konnte nicht geklickt werden: {e}")
                except Exception:
                    pass
                return False
            await asyncio.sleep(0.6)
    return False

# -------------------------
# Scraping-Funktion (Hauptlogik)
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

            # 1) Seite öffnen
            await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(1.0)

            # 2) Overlays robust entfernen (mehrere Strategien)
            await ensure_no_overlays(page)

            # 3) Filter öffnen (robust)
            ok = await safe_click(
                page,
                "button[aria-label='Filter öffnen']",
                description="Filter öffnen",
                alt_selectors=["button[aria-label='Filter']", "button:has-text('Filter')", "text=Filter"]
            )
            if not ok:
                print("❌ Filter konnte nicht geöffnet werden -> Abbruch dieses Laufs")
                # cleanup
                await context.close()
                await browser.close()
                return []

            # 4) Nochmal Overlays (falls eins nach Öffnen auftaucht)
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
                                # click checkbox
                                try:
                                    await cb.click()
                                    await asyncio.sleep(0.4)
                                    print(f"✅ '{label_text}' deaktiviert")
                                except Exception:
                                    # fallback: eval_on_selector on label to toggle
                                    try:
                                        await page.eval_on_selector(f"label:has-text('{label_text}')", "el => el.click()")
                                        await asyncio.sleep(0.4)
                                        print(f"✅ '{label_text}' via label-click deaktiviert (Fallback)")
                                    except Exception:
                                        print(f"⚠️ Konnte '{label_text}' nicht deaktivieren")
                except Exception as e:
                    print(f"⚠️ Fehler beim Deaktivieren von {label_text}: {e}")

            # 6) 'Einschränkungen' Tab öffnen
            ok = await safe_click(page, "text=Einschränkungen", description="Einschränkungen aktivieren",
                                  alt_selectors=["button:has-text('Einschränkungen')", "a:has-text('Einschränkungen')"])
            if not ok:
                print("❌ 'Einschränkungen' Tab konnte nicht aktiviert werden -> Abbruch dieses Laufs")
                await context.close()
                await browser.close()
                return []

            await asyncio.sleep(0.7)
            await ensure_no_overlays(page)

            # 7) Sortieren nach "Gültigkeit von" (zweimal)
            ok = await safe_click(page, 'th:has-text("Gültigkeit von")', description="Tabelle sortieren",
                                  alt_selectors=["table thead th:nth-last-child(2)"])
            if not ok:
                print("⚠️ Warnung: Sortierung konnte nicht angewendet (weiter mit aktueller Reihenfolge)")
            else:
                # zweiter Klick ruhiger: falls sortierung nötig nochmal versuchen
                await asyncio.sleep(0.35)
                try:
                    await page.eval_on_selector('th:has-text("Gültigkeit von")', "el => el.click()")
                    await asyncio.sleep(0.4)
                except Exception:
                    # ignore
                    pass

            # 8) Tabelle warten & auslesen
            await page.wait_for_selector("table tbody tr", timeout=15000)
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
# Prüf-Loop: Scrapen und an Discord senden
# -------------------------
async def check_stoerungen():
    global last_stoerungen, last_check_time
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        # optional start message
        try:
            await safe_send_to_channel(channel, "✅ Bahn-Störungs-Bot wurde gestartet!")
        except Exception:
            pass

    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()
        last_check_time = datetime.now()

        if stoerungen:
            channel = bot.get_channel(CHANNEL_ID)
            for s in stoerungen:
                if s["id"] not in last_stoerungen:
                    last_stoerungen.add(s["id"])
                    if channel:
                        success = await safe_send_to_channel(channel, s["text"])
                        if not success:
                            print(f"⚠️ Nachricht für {s['id']} konnte nicht gesendet werden.")
                    else:
                        print("⚠️ Channel nicht verfügbar - Nachricht nicht gesendet.")
        else:
            print("ℹ️ Keine neuen Störungen in diesem Durchlauf")

        await asyncio.sleep(600)  # 10 Minuten

# -------------------------
# Admin-Status-Command
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
# Main: Health-Webserver + Discord starten
# -------------------------
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
