import os
import discord
import asyncio
import requests
from bs4 import BeautifulSoup
from datetime import datetime

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
client = discord.Client(intents=intents)

# Speichert die zuletzt geposteten Störungs-IDs (oder Beschreibungen)
last_stoerungen = set()

def scrape_stoerungen():
    url = "https://strecken-info.de/"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Beispielhafte Struktur: Du musst die tatsächlichen Klassen/Tags anpassen!
        stoerungen_raw = soup.find_all("div", class_="störung")  # Beispiel

        stoerungen = []

        for item in stoerungen_raw:
            # Passe diese Felder je nach Webseite an:
            strecke = item.find("div", class_="strecke")
            grund = item.find("div", class_="grund")
            auswirkungen = item.find("div", class_="auswirkungen")
            beschreibung = item.find("div", class_="beschreibung")

            # Wenn eines fehlt, überspringen
            if not (strecke and grund and auswirkungen and beschreibung):
                continue

            stoerung = {
                "strecke": strecke.text.strip(),
                "grund": grund.text.strip(),
                "auswirkungen": auswirkungen.text.strip(),
                "beschreibung": beschreibung.text.strip()
            }

            stoerungen.append(stoerung)

        return stoerungen

    except Exception as e:
        print(f"[{datetime.now()}] Fehler beim Abrufen der Störungen: {e}")
        return []

async def check_stoerungen():
    await client.wait_until_ready()
    channel = client.get_channel(CHANNEL_ID)

    global last_stoerungen

    while not client.is_closed():
        stoerungen = scrape_stoerungen()

        if not stoerungen:
            print(f"[{datetime.now()}] Keine Störungen gefunden.")
        else:
            for s in stoerungen:
                unique_id = s["beschreibung"][:50]  # kurze Beschreibung als ID

                if unique_id not in last_stoerungen:
                    last_stoerungen.add(unique_id)

                    nachricht = (
                        f"🚨 **Störung auf der Strecke:** {s['strecke']}\n"
                        f"Grund: {s['grund']}\n"
                        f"Auswirkungen: {s['auswirkungen']}\n"
                        f"Beschreibung: {s['beschreibung']}"
                    )
                    try:
                        await channel.send(nachricht)
                        print(f"[{datetime.now()}] Neue Störung gepostet.")
                    except Exception as e:
                        print(f"Fehler beim Senden der Nachricht: {e}")

        await asyncio.sleep(600)  # alle 10 Minuten prüfen

@client.event
async def on_ready():
    print(f"Bot ist online als {client.user}")
    client.loop.create_task(check_stoerungen())

if __name__ == "__main__":
    client.run(TOKEN)
