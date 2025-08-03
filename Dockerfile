# Dockerfile für deinen Discord Bot mit Debugging

FROM python:3.11-slim

# Systemabhängigkeiten installieren (für Playwright etc.)
RUN apt-get update && apt-get install -y \
    curl \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libgtk-3-0 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Arbeitsverzeichnis
WORKDIR /app

# Kopiere requirements.txt
COPY requirements.txt .

# Installiere Python-Pakete
RUN pip install --no-cache-dir -r requirements.txt

# Playwright-Browser installieren
RUN playwright install --with-deps

# Kopiere den Bot-Code
COPY . .

# Exponiere Port für Debugger und Webserver
EXPOSE 5678 8080

# Startbefehl
CMD ["python", "bot.py"]
