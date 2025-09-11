import os, json, asyncio, traceback
from datetime import datetime
import discord
from discord.ext import commands
from playwright.async_api import async_playwright
from atproto import Client

# ============== DEBUG TEST (TEMPORÄR) ==============
print("🔍 STARTING DEBUG TEST...")

async def debug_test():
    print("🔍 Testing strecken-info.de structure...")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        
        try:
            print("🔍 Loading page...")
            await page.goto("https://strecken-info.de/", timeout=60000)
            await page.wait_for_load_state("networkidle", timeout=20000)
            
            title = await page.title()
            print(f"📄 Title: {title}")
            
            # Test basic elements
            body_text = await page.inner_text("body")
            print(f"📝 Body text length: {len(body_text)} chars")
            print(f"📝 First 300 chars: {body_text[:300]}...")
            
            # Test button finding
            buttons = await page.query_selector_all("button")
            print(f"🔘 {len(buttons)} buttons found")
            
            for i, btn in enumerate(buttons[:8]):
                try:
                    text = await btn.inner_text()
                    if text.strip():
                        print(f"  Button {i+1}: '{text.strip()}'")
                except:
                    pass
            
            # Test table finding  
            tables = await page.query_selector_all("table")
            print(f"📊 {len(tables)} tables found")
            
            all_rows = await page.query_selector_all("tr")
            print(f"📋 {len(all_rows)} total rows found")
            
            tbody_rows = await page.query_selector_all("table tbody tr")
            print(f"📋 {len(tbody_rows)} tbody rows found")
            
            # Test specific keywords
            keywords = ["Störung", "störung", "Einschränkung", "Baustelle", "Filter"]
            for keyword in keywords:
                count = body_text.lower().count(keyword.lower())
                print(f"🔍 '{keyword}': {count} occurrences")
            
            # Test filter elements
            filter_elements = await page.query_selector_all("*:has-text('Filter')")
            print(f"🔍 {len(filter_elements)} 'Filter' elements")
            
            # Test if we can click filter
            try:
                filter_btn = await page.query_selector("button:has-text('Filter')")
                if filter_btn:
                    print("✅ Filter button found and clickable")
                    await filter_btn.click(force=True)
                    await asyncio.sleep(3)
                    
                    # Check checkboxes after filter opened
                    checkboxes = await page.query_selector_all("input[type='checkbox']")
                    print(f"☑️ {len(checkboxes)} checkboxes found after filter click")
                    
                else:
                    print("❌ No Filter button found")
            except Exception as filter_e:
                print(f"❌ Filter click failed: {filter_e}")
            
        except Exception as e:
            print(f"❌ Test error: {e}")
            traceback.print_exc()
        finally:
            await browser.close()

# Führe Debug-Test aus und beende dann
asyncio.run(debug_test())
print("🔍 DEBUG TEST COMPLETE - EXITING BEFORE NORMAL BOT CODE")
exit(0)

# ============== NORMALER BOT CODE (wird nicht erreicht) ==============

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
                await page.click("button:has-text('Filter')", timeout=8000, force=True)
                print("✅ Filter geöffnet")
            except Exception as e: 
                print(f"⚠️ Filter-Button nicht gefunden: {e}")

            # Nur "Störungen" anhaken
            try:
                print("🔍 Setze Störungen-Filter...")
                selector = "label:has-text('Störungen') input[type='checkbox']"
                cb = await page.wait_for_selector(selector, timeout=5000)
                if not await cb.is_checked():
                    await cb.click(force=True)
                    print("✅ Störungen-Filter aktiviert")
                else:
                    print("ℹ️ Störungen-Filter bereits aktiv")
            except Exception as e: 
                print(f"⚠️ Störungen-Filter nicht gefunden: {e}")

            # WICHTIG: Tab "Störungen" wählen (NICHT Einschränkungen!)
            try:
                print("🔍 Klicke auf STÖRUNGEN-Tab...")
                # Mehrere Selektoren probieren
                tab_clicked = False
                
                # Versuch 1: Text-Selektor
                try:
                    await page.click("text=Störungen", timeout=5000, force=True)
                    tab_clicked = True
                    print("✅ Störungen-Tab (text) aktiviert")
                except:
                    pass
                
                # Versuch 2: Button-Selektor
                if not tab_clicked:
                    try:
                        await page.click("button:has-text('Störungen')", timeout=3000, force=True)
                        tab_clicked = True
                        print("✅ Störungen-Tab (button) aktiviert")
                    except:
                        pass
                
                # Versuch 3: Tab-Selektor
                if not tab_clicked:
                    try:
                        await page.click("[role='tab']:has-text('Störungen')", timeout=3000, force=True)
                        tab_clicked = True
                        print("✅ Störungen-Tab (role=tab) aktiviert")
                    except:
                        pass
                
                if not tab_clicked:
                    print("⚠️ Störungen-Tab nicht gefunden - verwende Standard-Ansicht")
                    
            except Exception as e: 
                print(f"⚠️ Fehler beim Tab-Wechsel: {e}")

            # Warten auf Tabelle
            print("🔍 Warte auf Tabelle...")
            await asyncio.sleep(8)  # Länger warten für Tab-Wechsel

            # Debug: Schaue welche Tabs verfügbar sind
            try:
                tabs = await page.query_selector_all("button, [role='tab']")
                tab_texts = []
                for tab in tabs[:10]:  # Nur erste 10
                    try:
                        text = await tab.inner_text()
                        if text.strip():
                            tab_texts.append(text.strip())
                    except:
                        pass
                print(f"🔍 Verfügbare Tabs/Buttons: {tab_texts}")
            except:
                pass

            # Tabelle laden
            rows = []
            for i in range(8):  # Mehr Versuche
                print(f"🔍 Versuch {i+1}/8: Lade Tabellenzeilen...")
                rows = await page.query_selector_all("table tbody tr")
                if rows: 
                    print(f"✅ {len(rows)} Zeilen gefunden")
                    break
                await asyncio.sleep(3)

            if not rows:
                print("❌ Keine Tabellenzeilen gefunden!")
                # Debug: Schaue was auf der Seite ist
                try:
                    # Schaue nach allen Tabellen
                    tables = await page.query_selector_all("table")
                    print(f"🔍 {len(tables)} Tabellen gefunden")
                    
                    # Schaue nach allen TR-Elementen
                    all_rows = await page.query_selector_all("tr")
                    print(f"🔍 {len(all_rows)} TR-Elemente insgesamt gefunden")
                    
                    # Schaue nach anderen möglichen Container
                    cards = await page.query_selector_all(".card, .item, .entry")
                    print(f"🔍 {len(cards)} Card/Item-Elemente gefunden")
                    
                except Exception as debug_e:
                    print(f"🔍 Debug-Fehler: {debug_e}")

            processed_count = 0
            for i, row in enumerate(rows):
                try:
                    cols = await row.query_selector_all("td")
                    if len(cols) < 8: 
                        print(f"🔍 Zeile {i+1}: Nur {len(cols)} Spalten, überspringe...")
                        continue
                        
                    id_text     = (await cols[0].inner_text()).strip()
                    typ         = (await cols[1].inner_text()).strip()
                    ort         = (await cols[2].inner_text()).strip()
                    region      = (await cols[3].inner_text()).strip()
                    wirkung     = (await cols[4].inner_text()).strip()
                    ursache     = (await cols[5].inner_text()).strip()
                    gueltig_von = (await cols[6].inner_text()).strip()
                    gueltig_bis = (await cols[7].inner_text()).strip()

                    print(f"🔍 Zeile {i+1}: ID={id_text}, Typ={typ}, Ort={ort}")

                    # Nur Streckenruhe überspringen - Baustellen sind auch wichtig!
                    if typ.lower() == "streckenruhe":
                        print(f"🔍 Überspringe {typ}: {id_text}")
                        continue
                    
                    # Für Baustellen anderen Emoji verwenden
                    emoji = "🚧" if typ.lower() == "baustelle" else "🚨"

                    stoerungen.append({
                        "id": id_text,
                        "typ": typ,
                        "ort": ort,
                        "region": region,
                        "wirkung": wirkung,
                        "ursache": ursache,
                        "gueltig_von": gueltig_von,
                        "gueltig_bis": gueltig_bis,
                        "discord_text": (
                            f"{emoji} **Neue Bahn-{typ}!**\n"
                            f"🆔 {id_text}\n📍 {ort}\n🗺️ {region}\n"
                            f"🚦 {wirkung}\n📋 {ursache}\n"
                            f"⏰ {gueltig_von} → {gueltig_bis}"
                        ),
                        "bsky_text": (
                            f"{emoji} Neue Bahn-{typ}!\n"
                            f"ID: {id_text}\nOrt: {ort}\nRegion: {region}\n"
                            f"Wirkung: {wirkung}\nUrsache: {ursache}\n"
                            f"⏰ {gueltig_von} → {gueltig_bis}"
                        )
                    })
                    processed_count += 1
                    print(f"✅ {typ} hinzugefügt: {id_text}")
                except Exception as e: 
                    print(f"❌ Fehler bei Zeile {i+1}: {e}")
                    continue

            print(f"🔍 Scraping abgeschlossen: {processed_count} Einträge gefunden")

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

            state[s["id"]] = {"typ": s["typ"], "ort": s["ort"]}  # Mehr Info speichern
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
    await bot.close()  # wichtig, sonst hängt GitHub Action ewig

# ---------------- Start ----------------
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
