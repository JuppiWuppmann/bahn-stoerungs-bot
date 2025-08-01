import discord
import requests
import asyncio

TOKEN = 'DEIN_DISCORD_BOT_TOKEN_HIER'

intents = discord.Intents.default()
client = discord.Client(intents=intents)

async def check_stoerungen():
    await client.wait_until_ready()
    channel = client.get_channel(DEINE_CHANNEL_ID)  # ID von deinem Discord-Kanal (Zahl)
    while not client.is_closed():
        try:
            # Beispiel: Daten von Strecken-Info.de abrufen (angenommen JSON-API)
            url = 'https://www.strecken-info.de/api/stoerungen'  # Beispiel-URL (bitte prüfen)
            response = requests.get(url)
            data = response.json()
            
            # Hier kannst du filtern, ob neue Störungen da sind
            # Zum Beispiel nur den ersten Eintrag anzeigen
            if data and len(data) > 0:
                stoerung = data[0]
                nachricht = (
                    f"🚨 **Störung auf der Strecke:** {stoerung['strecke']}\n"
                    f"Grund: {stoerung['grund']}\n"
                    f"Auswirkungen: {stoerung['auswirkungen']}\n"
                    f"Beschreibung: {stoerung['beschreibung']}"
                )
                await channel.send(nachricht)
            else:
                await channel.send("Keine aktuellen Störungen.")
        except Exception as e:
            print(f"Fehler: {e}")
        
        await asyncio.sleep(600)  # Warte 10 Minuten bis zum nächsten Check

@client.event
async def on_ready():
    print(f'Eingeloggt als {client.user}')
    client.loop.create_task(check_stoerungen())

client.run(TOKEN)
