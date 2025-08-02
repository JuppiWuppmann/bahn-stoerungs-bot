FROM python:3.11-slim

# Notwendige Bibliotheken für Playwright (inkl. GTK4 usw.)
RUN apt-get update && apt-get install -y \
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
    libgtk-4-1 \
    libdrm2 \
    libdbus-1-3 \
    libxss1 \
    libcurl4 \
    libgraphene-1.0-0 \
    libgstgl1.0-0 \
    libgstcodecparsers-1.0-0 \
    libenchant-2-2 \
    libsecret-1-0 \
    libmanette-0.2-0 \
    libgles2 \
    wget \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Arbeitsverzeichnis setzen
WORKDIR /app

# Abhängigkeiten installieren
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Playwright-Browser installieren
RUN pip install playwright && playwright install

# Restliche Dateien kopieren
COPY . .

# Port für Render (z. B. 10000)
ENV PORT=10000

# Startbefehl
CMD ["python", "main.py"]
