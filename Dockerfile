FROM python:3.12

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --timeout=300 -r requirements.txt

COPY . .

RUN mkdir -p tmp

ENV CACHE_SIZE=1000
ENV OUTPUT_QUALITY=95
ENV MAX_WORKERS=1
ENV TEMP_DIR=tmp
ENV KEEP_TEMP_FILES=False
ENV PORT=3000

EXPOSE 3000

CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1 --log-level debug --timeout-keep-alive 60