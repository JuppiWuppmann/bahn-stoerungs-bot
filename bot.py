import os
import asyncio
from datetime import datetime
from discord.ext import commands
import discord
from aiohttp import web
from playwright.async_api import async_playwright, Error as PlaywrightError
from io import BytesIO

# -------------------------
# Konfiguration / Umgebungsvariablen
# -------------------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
ADMIN_ID = os.getenv("ADMIN_ID")

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
# Hilfsfunktionen: Screenshot senden
# -------------------------
async def send_screenshot(page, fehlertext="Fehler"):
    try:
        channel = bot.get_channel(CHANNEL_ID)
        if channel:
            screenshot_bytes = await page.screenshot(type="png", full_page=False)
            buffer = BytesIO(screenshot_bytes)
            buffer.name = "screenshot.png"
            buffer.seek(0)
            await channel.send(
                content=f"❌ **Fehler beim Scraping:** {fehlertext}",
                file=discord.File(fp=buffer, filename="screenshot.png")
            )
    except Exception as e:
        print("⚠️ Fehler beim Screenshot-Senden:", e)

# -------------------------
# Robustes Schließen von Overlays/Popups (mehrere Strategien)
# -------------------------
async def close_overlays(page, max_wait_seconds: float = 8.0):
    """
    Versucht in einer kurzen Schleife alle störenden Overlays (Cookie, Analyse, Info-Dialoge)
    zu schließen. Lässt sich mehrfach wiederholen.
    """
    start = datetime.now().timestamp()
    while datetime.now().timestamp() - start < max_wait_seconds:
        closed_any = False

        # Varianten: "Ablehnen", "Alle akzeptieren", "Alles akzeptieren"
        try:
            btn = await page.query_selector("button:has-text('Ablehnen')")
            if btn:
                await btn.click()
                await asyncio.sleep(0.6)
                print("✅ Overlay: 'Ablehnen' geklickt")
                closed_any = True
        except Exception:
            pass

        try:
            btn = await page.query_selector("button:has-text('Alle akzeptieren')")
            if btn:
                await btn.click()
                await asyncio.sleep(0.6)
                print("✅ Overlay: 'Alle akzeptieren' geklickt")
                closed_any = True
        except Exception:
            pass

        try:
            btn = await page.query_selector("button:has-text('Alles akzeptieren')")
            if btn:
                await btn.click()
                await asyncio.sleep(0.6)
                print("✅ Overlay: 'Alles akzeptieren' geklickt")
                closed_any = True
        except Exception:
            pass

        # Blaues Info-Dialog (role=dialog) - Suche nach Close-Button
        try:
            dlg_close = await page.query_selector("div[role='dialog'] button[aria-label='Close']")
            if dlg_close:
                await dlg_close.click()
                await asyncio.sleep(0.6)
                print("✅ Info-Dialog geschlossen (aria-label='Close')")
                closed_any = True
        except Exception:
            pass

        # Alternative: generischer dialog-close
        try:
            dlg_close2 = await page.query_selector("div[role='dialog'] button:has-text('Schließen')")
            if dlg_close2:
                await dlg_close2.click()
                await asyncio.sleep(0.6)
                print("✅ Info-Dialog geschlossen (button 'Schließen')")
                closed_any = True
        except Exception:
            pass

        # MUI-Dialog spezifisch (DB Seite verwendet oft MUI)
        try:
            mui_btn = await page.query_selector("div[class*=MuiDialog] button")
            if mui_btn:
                # try to click only if visible
                await mui_btn.click()
                await asyncio.sleep(0.6)
                print("✅ MUI-Dialog-Button geklickt")
                closed_any = True
        except Exception:
            pass

        # Wenn nichts geschlossen wurde, beenden
        if not closed_any:
            break

    # kleine Pause am Ende
    await asyncio.sleep(0.2)

# -------------------------
# Scraping Funktion (Hauptlogik)
# -------------------------
async def scrape_stoerungen():
    global last_stoerungen
    print(f"[{datetime.now()}] 🔁 scrape_stoerungen gestartet")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True,
                                              args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(viewport={"width": 1366, "height": 900})
            page = await context.new_page()

            # user agent kann helfen falls website special casing macht
            await page.set_extra_http_headers({"Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})

            # 1) Seite öffnen
            await page.goto("https://strecken-info.de/", timeout=60000)
            await page.wait_for_load_state("networkidle")
            await asyncio.sleep(1)

            # 2) Popups schließen (robust)
            await close_overlays(page, max_wait_seconds=6)

            # 3) Filter öffnen: mehrere Selektoren als Fallback
            filter_button = None
            filter_selectors = [
                "button[aria-label='Filter öffnen']",
                "button[aria-label='Filter']",
                "button:has-text('Filter')",
                "text=Filter"
            ]
            for sel in filter_selectors:
                try:
                    elem = await page.query_selector(sel)
                    if elem:
                        filter_button = elem
                        break
                except Exception:
                    pass

            if not filter_button:
                # Letzte Chance: warte kurz, schließe overlays und versuche wieder
                await close_overlays(page, max_wait_seconds=3)
                for sel in filter_selectors:
                    try:
                        elem = await page.query_selector(sel)
                        if elem:
                            filter_button = elem
                            break
                    except Exception:
                        pass

            if not filter_button:
                await send_screenshot(page, "Filter-Panel konnte nicht geöffnet werden: Button nicht gefunden")
                print("❌ Filter-Button nicht gefunden - Abbruch")
                await context.close()
                await browser.close()
                return []

            try:
                await filter_button.scroll_into_view_if_needed()
                await filter_button.click()
                await asyncio.sleep(1.2)
                print("✅ Filter-Panel geöffnet")
            except Exception as e:
                await send_screenshot(page, f"Fehler beim Klick auf Filter-Button: {e}")
                print("❌ Fehler beim Klick auf Filter-Button:", e)
                await context.close()
                await browser.close()
                return []

            # erneut Popups schließen (falls eines nach Filter-Öffnung erscheint)
            await close_overlays(page, max_wait_seconds=4)

            # 4) Baustellen & Streckenruhen deaktivieren; Störungen aktiv lassen
            for label_text in ["Baustellen", "Streckenruhen"]:
                try:
                    label = await page.query_selector(f"label:has-text('{label_text}')")
                    if label:
                        cb = await label.query_selector("input[type='checkbox']")
                        if cb:
                            try:
                                checked = await cb.is_checked()
                            except Exception:
                                checked = False
                            if checked:
                                await cb.click()
                                await asyncio.sleep(0.6)
                                print(f"✅ '{label_text}' deaktiviert")
                            else:
                                print(f"ℹ️ '{label_text}' war bereits deaktiviert")
                except Exception as e:
                    print(f"⚠️ Fehler beim Deaktivieren von {label_text}: {e}")

            # optional: stelle sicher dass "Störungen" angehakt ist (wenn vorhanden)
            try:
                stoer_label = await page.query_selector("label:has-text('Störungen')")
                if stoer_label:
                    cb = await stoer_label.query_selector("input[type='checkbox']")
                    if cb:
                        try:
                            checked = await cb.is_checked()
                        except Exception:
                            checked = False
                        if not checked:
                            await cb.click()
                            await asyncio.sleep(0.6)
                            print("✅ 'Störungen' aktiviert")
            except Exception:
                pass

            # 5) Einschränkungen-Tab klicken (falls vorhanden)
            try:
                # mehrere Varianten: button text oder nav item
                tab_selectors = ["text=Einschränkungen", "button:has-text('Einschränkungen')", "a:has-text('Einschränkungen')"]
                clicked_tab = False
                for ts in tab_selectors:
                    try:
                        elem = await page.query_selector(ts)
                        if elem:
                            await elem.click()
                            await asyncio.sleep(0.8)
                            clicked_tab = True
                            print("✅ 'Einschränkungen' Tab geöffnet")
                            break
                    except Exception:
                        pass
                if not clicked_tab:
                    # evtl ist die Tabelle schon sichtbar ohne Tab-Klick
                    print("ℹ️ 'Einschränkungen' Tab nicht gefunden (vielleicht schon aktiv)")
            except Exception as e:
                print("⚠️ Fehler beim Öffnen von 'Einschränkungen':", e)

            # nochmal Overlays schließen
            await close_overlays(page, max_wait_seconds=3)

            # 6) Sortierung: "Gültigkeit von" doppelklick (neueste zuerst)
            try:
                # Warte kurz, dann click twice
                sort_selector = 'th:has-text("Gültigkeit von")'
                sort_elem = await page.wait_for_selector(sort_selector, timeout=7000)
                await sort_elem.click()
                await asyncio.sleep(0.4)
                await sort_elem.click()
                await asyncio.sleep(0.6)
                print("✅ Sortierung: 'Gültigkeit von' zweimal geklickt")
            except Exception as e:
                await send_screenshot(page, f"Sortierung fehlgeschlagen: {e}")
                print("⚠️ Sortierung fehlgeschlagen:", e)
                # wir brechen nicht komplett ab hier, sondern versuchen trotzdem die Tabelle zu lesen

            # 7) Tabelle warten & Zeilen auslesen
            try:
                await page.wait_for_selector("table tbody tr", timeout=15000)
                rows = await page.query_selector_all("table tbody tr")
                print(f"🔍 Gefundene Tabellenzeilen: {len(rows)}")
            except Exception as e:
                await send_screenshot(page, f"Tabelle nicht gefunden: {e}")
                print("❌ Tabelle nicht gefunden:", e)
                await context.close()
                await browser.close()
                return []

            # 8) Auslesen der Tabellenzeilen
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
                    print("⚠️ Fehler beim Auslesen einer Zeile:", e)
                    continue

            print(f"🔍 Neue Störungen erkannt: {len(new_stoerungen)}")

            # cleanup browser context
            await context.close()
            await browser.close()

            return new_stoerungen

    except PlaywrightError as e:
        print("❌ Playwright-Fehler beim Scraping:", e)
        return []
    except Exception as e:
        print("❌ Unerwarteter Fehler beim Scraping:", e)
        return []

# -------------------------
# Prüf-Loop: Scrapen und an Discord senden
# -------------------------
async def check_stoerungen():
    global last_stoerungen, last_check_time
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("✅ Bahn-Störungs-Bot wurde gestartet!")

    while not bot.is_closed():
        stoerungen = await scrape_stoerungen()
        last_check_time = datetime.now()

        # Sende neue Störungen an Discord
        if stoerungen:
            channel = bot.get_channel(CHANNEL_ID)
            for s in stoerungen:
                if s["id"] not in last_stoerungen:
                    last_stoerungen.add(s["id"])
                    try:
                        if channel:
                            await channel.send(s["text"])
                            print(f"✅ Gesendet: {s['id']}")
                        else:
                            print("⚠️ Channel nicht gefunden, kann Nachricht nicht senden")
                    except Exception as e:
                        print("❌ Fehler beim Senden an Discord:", e)
        else:
            print("ℹ️ Keine neuen Störungen in diesem Durchlauf")

        # Warte 10 Minuten
        await asyncio.sleep(600)

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
    # starte Loop
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
