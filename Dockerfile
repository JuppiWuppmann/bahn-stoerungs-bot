FROM python:3.10-slim

WORKDIR /app

# System-Updates und grundlegende Dependencies
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
    libxshmfence1 \
    libdrm2 \
    fonts-liberation \
    libappindicator3-1 \
    libxkbcommon0 \
    libxcb-dri3-0 \
    libgbm-dev \
    fonts-unifont \
    fonts-ubuntu \
    && apt-get clean

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Installiere nur den Chromium-Browser, ohne Systempakete
RUN python -m playwright install chromium

COPY . .

CMD ["python", "bot.py"]
