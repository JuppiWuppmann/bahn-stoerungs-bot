FROM python:3.11-slim

# System-Abhängigkeiten für Playwright und Node.js installieren
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libgbm1 \
    libxkbcommon0 \
    libasound2 \
    libpangocairo-1.0-0 \
    libgtk-3-0 \
    libdrm2 \
    libdbus-1-3 \
    libxss1 \
    libcurl4 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Arbeitsverzeichnis setzen
WORKDIR /app

# Python-Abhängigkeiten installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright installieren (über pip)
RUN pip install playwright

# Playwright-Browser herunterladen
RUN playwright install chromium

# Projektdateien kopieren
COPY . .

# Startbefehl
CMD ["python", "main.py"]
