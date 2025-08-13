FROM python:3.10-slim

WORKDIR /app

# 1. Python-Abhängigkeiten
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# 2. Systembibliotheken für Playwright / Chromium
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxcomposite1 \
    libxrandr2 \
    libgbm1 \
    libgtk-3-0 \
    libxdamage1 \
    libxfixes3 \
    libxrender1 \
    libasound2 \
    libx11-xcb1 \
    libxss1 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    fonts-unifont \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# 3. Playwright-Browser separat installieren (ohne --with-deps)
RUN python -m playwright install chromium

# 4. Bot-Code kopieren
COPY . .

CMD ["python", "bot.py"]
