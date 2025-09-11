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

# ---------------- Helper Functions ----------------
def is_valid_stoerung(id_text, typ):
    """Filtere ungültige Einträge heraus"""
    # Header-Zeilen ignorieren
    if not id_text or id_text.strip() in ["ID", "id", "ID\n0"]:
        return False
    
    # Newlines in ID sind ein Zeichen für Header
    if "\n" in id_text:
        return False
    
    # Typ muss gültig sein
    if not typ or typ.strip().lower() in ["typ", "type", "typ\n0"]:
        return False
    
    # Nur bestimmte Typen erlauben
    valid_types = ["störung", "baustelle", "sperrung", "einschränkung"]
    if not any(vtype in typ.lower() for vtype in valid_types):
        return False
        
    return True

def should_notify_immediately(typ, wirkung):
    """Bestimme ob sofort gepostet werden soll (für akute Störungen)"""
    # Akute Störungen sofort posten
    if "störung" in typ.lower():
        return True
    
    # Totalsperrungen auch sofort posten
    if "totalsperrung" in wirkung.lower():
        return True
        
    # Baustellen können warten (weniger spam)
    return False

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

            # Filter öffnen
            try:
                print("🔍 Öffne Filter...")
                await page.wait_for_selector("button:has-text('Filter')", timeout=10000)
                await page.click("button:has-text('Filter')", timeout=8000, force=True)
                await asyncio.sleep(2)
                print("✅ Filter geöffnet")
            except Exception as e: 
                print(f"⚠️ Filter-Button nicht gefunden: {e}")

            # BEIDE Filter aktivieren: Störungen UND Baustellen
            try:
                print("🔍 Aktiviere Filter...")
                
                await page.wait_for_selector("input[type='checkbox']", timeout=5000)
                checkboxes = await page.query_selector_all("input[type='checkbox']")
                
                for cb in checkboxes:
                    try:
                        # Schaue nach dem Parent-Element für den Text
                        parent = await cb.query_selector("xpath=..")
                        if parent:
                            parent_text = await parent.inner_text()
                            
                            # Störungen und Baustellen aktivieren, Streckenruhe deaktivieren
                            if "störung" in parent_text.lower():
                                if not await cb.is_checked():
                                    await cb.click(force=True)
                                    print("✅ Störungen aktiviert")
                            elif "baustell" in parent_text.lower():
                                if not await cb.is_checked():
                                    await cb.click(force=True)
                                    print("✅ Baustellen aktiviert")
                            elif "streckenruhe" in parent_text.lower():
                                if await cb.is_checked():
                                    await cb.click(force=True)
                                    print("❌ Streckenruhe deaktiviert")
                                    
                    except Exception as cb_e:
                        continue

            except Exception as e: 
                print(f"⚠️ Filter-Aktivierung fehlgeschlagen: {e}")

            # Auf "Einschränkungen" Tab wechseln
            try:
                print("🔍 Wechsle zu Einschränkungen-Tab...")
                await page.wait_for_selector("button:has-text('Einschränkungen')", timeout=10000)
                await page.click("button:has-text('Einschränkungen')", timeout=5000, force=True)
                await asyncio.sleep(4)
                print("✅ Einschränkungen-Tab aktiviert")
            except Exception as e: 
                print(f"⚠️ Einschränkungen-Tab nicht gefunden: {e}")

            # Warten auf Tabelle
            print("🔍 Warte auf Tabelle...")
            await asyncio.sleep(6)

            # Tabellen-Suche
            for attempt in range(3):
                await asyncio.sleep(2)
                tables = await page.query_selector_all("table")
                if tables:
                    print(f"✅ {len(tables)} Tabellen gefunden")
                    break
                print(f"🔍 Versuch {attempt+1}/3: Noch keine Tabellen...")

            rows = await page.query_selector_all("table tbody tr, table tr")
            print(f"🔍 {len(rows)} Zeilen gefunden")

            processed_count = 0
            skipped_count = 0

            for i, row in enumerate(rows):
                try:
                    cols = await row.query_selector_all("td, th")
                    if len(cols) < 6:  # Mindestens 6 Spalten erwartet
                        continue
                        
                    # Extrahiere Daten aus den Spalten
                    col_texts = []
                    for col in cols:
                        text = (await col.inner_text()).strip()
                        col_texts.append(text)
                    
                    id_text = col_texts[0]
                    typ = col_texts[1] if len(col_texts) > 1 else "Unbekannt"
                    
                    # Validierung der Daten
                    if not is_valid_stoerung(id_text, typ):
                        print(f"🔍 Zeile {i+1} übersprungen (Header/Invalid): {id_text}")
                        skipped_count += 1
                        continue
                    
                    print(f"🔍 Zeile {i+1}: ID={id_text}, Typ={typ}")
                    
                    # Störung erstellen
                    stoerung = {
                        "id": id_text,
                        "typ": typ,
                        "ort": col_texts[2] if len(col_texts) > 2 else "Unbekannt",
                        "region": col_texts[3] if len(col_texts) > 3 else "Unbekannt",
                        "wirkung": col_texts[4] if len(col_texts) > 4 else "Unbekannt",
                        "ursache": col_texts[5] if len(col_texts) > 5 else "Unbekannt",
                        "gueltig_von": col_texts[6] if len(col_texts) > 6 else "Jetzt",
                        "gueltig_bis": col_texts[7] if len(col_texts) > 7 else "Unbekannt",
                        "priority": "high" if should_notify_immediately(typ, col_texts[4] if len(col_texts) > 4 else "") else "low"
                    }
                    
                    # Emoji basierend auf Typ
                    if "störung" in typ.lower():
                        emoji = "🚨"
                    elif "baustell" in typ.lower():
                        emoji = "🚧"
                    else:
                        emoji = "⚠️"
                    
                    stoerung["discord_text"] = (
                        f"{emoji} **Neue Bahn-{stoerung['typ']}!**\n"
                        f"🆔 {stoerung['id']}\n📍 {stoerung['ort']}\n🗺️ {stoerung['region']}\n"
                        f"🚦 {stoerung['wirkung']}\n📋 {stoerung['ursache']}\n"
                        f"⏰ {stoerung['gueltig_von']} → {stoerung['gueltig_bis']}"
                    )
                    
                    stoerung["bsky_text"] = (
                        f"{emoji} Neue Bahn-{stoerung['typ']}!\n"
                        f"ID: {stoerung['id']}\nOrt: {stoerung['ort']}\nRegion: {stoerung['region']}\n"
                        f"Wirkung: {stoerung['wirkung']}\nUrsache: {stoerung['ursache']}\n"
                        f"⏰ {stoerung['gueltig_von']} → {stoerung['gueltig_bis']}"
                    )

                    stoerungen.append(stoerung)
                    processed_count += 1
                    print(f"✅ {typ} hinzugefügt: {id_text}")

                except Exception as row_e:
                    print(f"❌ Fehler bei Zeile {i+1}: {row_e}")
                    continue

            print(f"🔍 Scraping abgeschlossen: {processed_count} gültige Einträge, {skipped_count} übersprungen")

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

async def send_discord_batch(messages: list, batch_size=5):
    """Sende mehrere Nachrichten in Batches um Spam zu vermeiden"""
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        return
        
    for i in range(0, len(messages), batch_size):
        batch = messages[i:i+batch_size]
        
        if len(batch) == 1:
            # Einzelnachricht
            await send_discord(batch[0])
        else:
            # Batch-Nachricht
            combined = f"🔄 **{len(batch)} neue Einträge:**\n\n" + "\n\n---\n\n".join(batch)
            if len(combined) > 2000:  # Discord Limit
                # Aufteilen wenn zu lang
                for msg in batch:
                    await send_discord(msg)
                    await asyncio.sleep(1)  # Rate limiting
            else:
                await send_discord(combined)
        
        if i + batch_size < len(messages):
            await asyncio.sleep(2)  # Pause zwischen Batches

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

def send_bluesky_batch(messages: list):
    """Sende Bluesky Batch-Nachrichten"""
    if len(messages) <= 3:
        for msg in messages:
            send_bluesky(msg)
    else:
        # Zusammenfassung für viele Nachrichten
        summary = f"🔄 {len(messages)} neue Bahn-Einträge gefunden! Details im Discord-Channel."
        send_bluesky(summary)

# ---------------- Main ----------------
async def check_and_post():
    print("🔍 Lade gespeicherten State...")
    state = load_state()
    print(f"🔍 {len(state)} bereits bekannte Einträge")
    
    stoerungen = await scrape_stoerungen()
    print(f"🔍 {len(stoerungen)} aktuelle Einträge gefunden")

    new_found = False
    resolved_count = 0
    
    # Neue Störungen nach Priorität sortieren
    new_stoerungen = [s for s in stoerungen if s["id"] not in state]
    high_priority = [s for s in new_stoerungen if s["priority"] == "high"]
    low_priority = [s for s in new_stoerungen if s["priority"] == "low"]
    
    print(f"🔍 {len(high_priority)} prioritäre Störungen, {len(low_priority)} normale Baustellen")
    
    # Prioritäre Störungen sofort einzeln posten
    for s in high_priority:
        print(f"🚨 PRIORITÄR: {s['id']} ({s['typ']}) - {s['ort']}")
        await send_discord(s["discord_text"])
        send_bluesky(s["bsky_text"])
        state[s["id"]] = {"typ": s["typ"], "ort": s["ort"], "priority": "high"}
        new_found = True
        await asyncio.sleep(1)  # Rate limiting

    # Normale Baustellen in Batches (weniger Spam)
    if low_priority:
        print(f"🔍 Poste {len(low_priority)} Baustellen in Batches...")
        discord_messages = [s["discord_text"] for s in low_priority]
        bsky_messages = [s["bsky_text"] for s in low_priority]
        
        await send_discord_batch(discord_messages, batch_size=3)
        send_bluesky_batch(bsky_messages)
        
        for s in low_priority:
            state[s["id"]] = {"typ": s["typ"], "ort": s["ort"], "priority": "low"}
            new_found = True

    # Behobene Einträge
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
        resolved_message = f"✅ **{resolved_count} Einträge behoben/beendet!**\n🆔 {', '.join(resolved_ids[:10])}"
        if len(resolved_ids) > 10:
            resolved_message += f"\n... und {len(resolved_ids)-10} weitere"
        
        await send_discord(resolved_message)
        send_bluesky(f"✅ {resolved_count} Einträge behoben/beendet!")
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
