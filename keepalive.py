from flask import Flask
import threading

app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

@app.route('/health')
def health():
    return "OK", 200

def run():
    # Render erwartet, dass dein Service auf 0.0.0.0:8080 läuft
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()
