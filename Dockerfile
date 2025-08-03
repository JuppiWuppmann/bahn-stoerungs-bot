FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 \
    libgbm1 libxkbcommon0 libasound2 libpangocairo-1.0-0 libgtk-3-0 libdrm2 libdbus-1-3 \
    libxss1 libcurl4 libglib2.0-0 \
    libgtk-4-1 libgraphene-1.0-0 libgstreamer-gl1.0-0 libgstreamer-plugins-base1.0-0 \
    libenchant-2-2 libsecret-1-0 libgles2-mesa libsndfile1 libpulse0 \
    libatomic1 libxslt1.1 libwoff2dec1 libvpx6 libevent-2.1-7 \
    libflite1 libwebpdemux2 libavif15 libharfbuzz-icu0 libwebpmux3 libhyphen0 \
    libx264-155 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install

COPY . .

CMD ["python", "main.py"]
