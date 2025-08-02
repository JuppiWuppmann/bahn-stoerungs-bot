FROM python:3.11-slim

# Playwright-abhängigkeiten installieren
RUN apt-get update && apt-get install -y \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 \
    libgbm1 libxkbcommon0 libasound2 libpangocairo-1.0-0 libgtk-3-0 libdrm2 libdbus-1-3 \
    libxss1 libcurl4 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install

COPY . .

CMD ["python", "main.py"]
