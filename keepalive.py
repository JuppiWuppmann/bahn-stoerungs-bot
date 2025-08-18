import asyncio
import threading
from flask import Flask
from bot import main  # Dein Bot-Einstiegspunkt

app = Flask(__name__)

@app.route("/")
def home():
    return "🚂 Bahn-Störungs-Bot läuft auf Render!"

def run_bot():
    asyncio.run(main())

if __name__ == "__main__":
    # Bot startet in separatem Thread
    threading.Thread(target=run_bot, daemon=True).start()
    # Dummy-Webserver für Render
    app.run(host="0.0.0.0", port=10000)
