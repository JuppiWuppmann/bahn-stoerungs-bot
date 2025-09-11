import os, json, asyncio, traceback
from datetime import datetime
import discord
from discord.ext import commands
from playwright.async_api import async_playwright
from atproto import Client

# ---------------- Konfiguration ----------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID    = int(os.getenv("CHANNEL_ID", "0"))
BSKY_HANDLE   = os.getenv("BSKY_HANDLE")
BSKY_PASSWORD = os.getenv("BSKY_PASSWORD")
STATE_FILE = "sent.json"
PAGE_LOAD_TIMEOUT = 80000

# ---------------- State ----------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ---------------- Scraper ----------------
async def scrape_stoerungen():
    print("🔍 Starte Scraping...")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        page = await context.new_page()
        stoerungen = []

        try:
            print("🔍 Lade Seite...")
            await page.goto("https://strecken-info.de/", timeout=PAGE_LOAD_TIMEOUT)
            await page.wait_for_load_state("networkidle", timeout=20000)
            print("✅ Seite geladen")

            # Overlays entfernen
            await page.evaluate("""
                document.getElementById('usercentrics-cmp-ui')?.remove();
                document.querySelector('.freiefahrt-yvnngg')?.remove();
            """)
            print("🔍 Overlays entfernt")

            # Filter öffnen - warten bis verfügbar
            try:
                print("🔍 Öffne Filter...")
                await page.wait_for_selector("button:has-text('Filter')", timeout=10000)
                await page.click("button:has-text('Filter')", timeout=8000, force=True)
                await asyncio.sleep(2)
                print("✅ Filter geöffnet")
            except Exception as e: 
                print(f"⚠️ Filter-Button nicht gefunden: {e}")

            # Störungen und Baustellen Filter aktivieren
            try:
                print("🔍 Aktiviere Störungen-Filter...")
                
                # Störungen aktivieren
                stoerungen_selector = "input[type='checkbox'][name*='störung' i], input[type='checkbox'] + label:has-text('Störung')"
                try:
                    await page.wait_for_selector("input[type='checkbox']", timeout=5000)
                    checkboxes = await page.query_selector_all("input[type='checkbox']")
                    
                    for cb in checkboxes:
                        # Schaue nach dem Label oder Namen
                        try:
                            parent = await cb.query_selector("xpath=..")
                            parent_text = await parent.inner_text() if parent else ""
                            
                            # Wenn es Störungen oder Baustellen enthält, aktivieren
                            if "störung" in parent_text.lower() or "baustell" in parent_text.lower():
                                is_checked = await cb.is_checked()
                                if not is_checked:
                                    await cb.click(force=True)
                                    print(f"✅ Aktiviert: {parent_text.strip()}")
                        except:
                            continue
                            
                except Exception as filter_e:
                    print(f"⚠️ Filter-Aktivierung fehlgeschlagen: {filter_e}")

            except Exception as e: 
                print(f"⚠️ Filter-Konfiguration Fehler: {e}")

            # Auf "Einschränkungen" Tab wechseln (hier sind die Daten)
            try:
                print("🔍 Wechsle zu Einschränkungen-Tab...")
                await page.wait_for_selector("button:has-text('Einschränkungen')", timeout=10000)
                await page.click("button:has-text('Einschränkungen')", timeout=5000, force=True)
                await asyncio.sleep(3)
                print("✅ Einschränkungen-Tab aktiviert")
            except Exception as e: 
                print(f"⚠️ Einschränkungen-Tab nicht gefunden: {e}")

            # Warten auf Daten-Container statt Tabelle
            print("🔍 Warte auf Datencontainer...")
            await asyncio.sleep(8)

            # Verschiedene Selektoren für Daten probieren
            data_found = False
            stoerungen_data = []

            # Versuch 1: Suche nach divs mit Störungsdaten
            try:
                print("🔍 Suche nach Daten-Containern...")
                
                # Mögliche Container-Selektoren
                selectors = [
                    "div[class*='row'], div[class*='item'], div[class*='entry']",
                    ".list-item, .data-item, .disruption-item",
                    "div:has-text('ICE'), div:has-text('RB'), div:has-text('S')",
                ]
                
                for selector in selectors:
                    containers = await page.query_selector_all(selector)
                    print(f"🔍 {len(containers)} Container mit '{selector}' gefunden")
                    
                    for container in containers:
                        try:
                            text = await container.inner_text()
                            # Prüfe ob es Bahn-relevante Daten enthält
                            if any(keyword in text.lower() for keyword in ["ice", "rb", "s ", "störung", "baustell", "gleis"]):
                                print(f"📝 Potenzieller Datensatz: {text[:100]}...")
                                # Hier könntest du die Daten parsen
                                
                        except:
                            continue
                            
            except Exception as e:
                print(f"🔍 Container-Suche Fehler: {e}")

            # Versuch 2: Tabellen-Suche (falls doch vorhanden)
            try:
                print("🔍 Suche nach Tabellen...")
                
                # Warte länger auf Tabellen
                for attempt in range(5):
                    await asyncio.sleep(2)
                    tables = await page.query_selector_all("table")
                    if tables:
                        print(f"✅ {len(tables)} Tabellen gefunden")
                        break
                    print(f"🔍 Versuch {attempt+1}/5: Noch keine Tabellen...")

                rows = await page.query_selector_all("table tbody tr, table tr")
                print(f"🔍 {len(rows)} Zeilen gefunden")

                for i, row in enumerate(rows):
                    try:
                        cols = await row.query_selector_all("td, th")
                        if len(cols) < 3:  # Mindestens 3 Spalten erwartet
                            continue
                            
                        # Extrahiere Daten aus den Spalten
                        col_texts = []
                        for col in cols:
                            text = (await col.inner_text()).strip()
                            col_texts.append(text)
                        
                        print(f"🔍 Zeile {i+1}: {col_texts}")
                        
                        # Wenn genug Daten vorhanden, als Störung behandeln
                        if len(col_texts) >= 6 and any(col_texts[0]):  # ID nicht leer
                            stoerungen.append({
                                "id": col_texts[0],
                                "typ": col_texts[1] if len(col_texts) > 1 else "Unbekannt",
                                "ort": col_texts[2] if len(col_texts) > 2 else "Unbekannt",
                                "region": col_texts[3] if len(col_texts) > 3 else "Unbekannt",
                                "wirkung": col_texts[4] if len(col_texts) > 4 else "Unbekannt",
                                "ursache": col_texts[5] if len(col_texts) > 5 else "Unbekannt",
                                "gueltig_von": col_texts[6] if len(col_texts) > 6 else "Jetzt",
                                "gueltig_bis": col_texts[7] if len(col_texts) > 7 else "Unbekannt",
                            })
                            print(f"✅ Störung hinzugefügt: {col_texts[0]}")

                    except Exception as row_e:
                        print(f"❌ Fehler bei Zeile {i+1}: {row_e}")
                        continue

            except Exception as e:
                print(f"🔍 Tabellen-Suche Fehler: {e}")

            # Debug: Seitencontent ausgeben
            if not stoerungen:
                print("🔍 Keine Störungen gefunden - Debug-Ausgabe:")
                try:
                    body_text = await page.inner_text("body")
                    relevant_text = [line for line in body_text.split('\n') 
                                   if any(word in line.lower() for word in ['störung', 'baustell', 'ice', 'rb', 'sperrung'])]
                    if relevant_text:
                        print("🔍 Relevante Zeilen gefunden:")
                        for line in relevant_text[:10]:
                            print(f"  📝 {line.strip()}")
                    else:
                        print("🔍 Keine relevanten Zeilen im Body-Text")
                except:
                    pass

            # Nachrichten für gefundene Störungen erstellen
            for s in stoerungen:
                # Emoji basierend auf Typ
                if "baustell" in s["typ"].lower():
                    emoji = "🚧"
                elif "störung" in s["typ"].lower():
                    emoji = "🚨"
                else:
                    emoji = "⚠️"
                
                s["discord_text"] = (
                    f"{emoji} **Neue Bahn-{s['typ']}!**\n"
                    f"🆔 {s['id']}\n📍 {s['ort']}\n🗺️ {s['region']}\n"
                    f"🚦 {s['wirkung']}\n📋 {s['ursache']}\n"
                    f"⏰ {s['gueltig_von']} → {s['gueltig_bis']}"
                )
                
                s["bsky_text"] = (
                    f"{emoji} Neue Bahn-{s['typ']}!\n"
                    f"ID: {s['id']}\nOrt: {s['ort']}\nRegion: {s['region']}\n"
                    f"Wirkung: {s['wirkung']}\nUrsache: {s['ursache']}\n"
                    f"⏰ {s['gueltig_von']} → {s['gueltig_bis']}"
                )

            print(f"🔍 Scraping abgeschlossen: {len(stoerungen)} Einträge gefunden")

        except Exception as e:
            print("❌ Fehler beim Scraping:", e)
            traceback.print_exc()
        finally:
            await context.close()
            await browser.close()

        return stoerungen

# ---------------- Discord ----------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

async def send_discord(message: str):
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        try:
            await channel.send(message)
            print("✅ Discord gepostet")
        except Exception as e:
            print("❌ Discord-Fehler:", e)

# ---------------- Bluesky ----------------
def split_message(text, limit=300):
    parts, cur = [], ""
    for word in text.split():
        if len(cur) + len(word) + 1 > limit:
            parts.append(cur.strip())
            cur = word
        else:
            cur += " " + word
    if cur.strip():
        parts.append(cur.strip())
    return parts

def send_bluesky(message: str):
    try:
        client = Client()
        client.login(BSKY_HANDLE, BSKY_PASSWORD)

        parts = split_message(message, 300)
        reply_ref = None

        for part in parts:
            post = client.send_post(part, reply_to=reply_ref)
            reply_ref = post
        print(f"✅ Bluesky: {len(parts)} Teile gepostet")
    except Exception as e:
        print("❌ Bluesky-Fehler:", e)

# ---------------- Main ----------------
async def check_and_post():
    print("🔍 Lade gespeicherten State...")
    state = load_state()
    print(f"🔍 {len(state)} bereits bekannte Einträge")
    
    if state:
        print("🔍 Bekannte IDs:", list(state.keys())[:10], "..." if len(state) > 10 else "")
    
    stoerungen = await scrape_stoerungen()
    print(f"🔍 {len(stoerungen)} aktuelle Einträge gefunden")

    if stoerungen:
        print("🔍 Aktuelle IDs:", [s["id"] for s in stoerungen[:10]], "..." if len(stoerungen) > 10 else "")
    
    new_found = False
    resolved_count = 0
    
    # Neue Störungen/Baustellen finden
    for s in stoerungen:
        if s["id"] not in state:
            print(f"👉 Neuer Eintrag gefunden: {s['id']} ({s['typ']}) - {s['ort']}")

            await send_discord(s["discord_text"])
            send_bluesky(s["bsky_text"])

            state[s["id"]] = {"typ": s["typ"], "ort": s["ort"]}
            new_found = True

    # Behobene/abgeschlossene Einträge finden
    current_ids = {s["id"] for s in stoerungen}
    resolved_ids = []
    for stored_id in list(state.keys()):
        if stored_id not in current_ids:
            resolved_ids.append(stored_id)
            print(f"✅ Behoben/Beendet: {stored_id}")
            del state[stored_id]
            resolved_count += 1
    
    if resolved_ids:
        print(f"✅ {resolved_count} Einträge behoben/beendet")
        resolved_message = f"✅ **Einträge behoben/beendet!**\n🆔 {', '.join(resolved_ids[:10])}"
        if len(resolved_ids) > 10:
            resolved_message += f"\n... und {len(resolved_ids)-10} weitere"
        
        await send_discord(resolved_message)
        send_bluesky(f"✅ Behoben/Beendet! IDs: {', '.join(resolved_ids[:5])}{'...' if len(resolved_ids) > 5 else ''}")
        new_found = True

    if new_found:
        save_state(state)
        print("✅ State gespeichert")
    else:
        print("ℹ️ Keine Änderungen")

@bot.event
async def on_ready():
    print(f"🤖 Bot eingeloggt als {bot.user}")
    await check_and_post()
    await bot.close()

# ---------------- Start ----------------
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
