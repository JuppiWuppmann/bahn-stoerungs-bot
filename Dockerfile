FROM python:3.10-slim

WORKDIR /app

# System-Pakete für Chromium/Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget gnupg ca-certificates \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libxcomposite1 libxrandr2 libgbm1 libgtk-3-0 libxdamage1 \
    libxfixes3 libxrender1 libasound2 libx11-xcb1 libxss1 \
    libdrm2 libxshmfence1 libxcb1 \
    fonts-unifont fonts-dejavu fonts-liberation \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Python-Abhängigkeiten
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

# Nur Chromium-Browser für Playwright installieren (kein WebKit/Firefox)
RUN python -m playwright install --with-deps chromium

COPY . .

CMD ["python", "bot.py"]
