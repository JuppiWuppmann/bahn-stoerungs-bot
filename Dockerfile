FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Notwendige System-Pakete für Chromium/Playwright
RUN apt-get update && apt-get install -y \
    wget gnupg libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libxcomposite1 libxrandr2 libgbm1 libgtk-3-0 libxdamage1 \
    libxfixes3 libxrender1 libasound2 libx11-xcb1 libxss1 \
    fonts-unifont fonts-dejavu fonts-liberation \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Nur Chromium-Browser über Playwright installieren (ohne kaputtes --with-deps)
RUN python -m playwright install chromium

COPY . .

CMD ["python", "bot.py"]
