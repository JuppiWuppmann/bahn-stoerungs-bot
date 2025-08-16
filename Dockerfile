FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt
RUN apt-get update && apt-get install -y wget gnupg libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libxcomposite1 libxrandr2 libgbm1 libgtk-3-0 libxdamage1 libxfixes3 libxrender1 libasound2 libx11-xcb1 libxss1 && \
    apt-get clean

RUN python -m playwright install --with-deps chromium

COPY . .

CMD ["python", "bot.py"]
