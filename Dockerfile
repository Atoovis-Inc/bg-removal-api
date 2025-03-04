FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# RUN python -m venv /app/venv
# ENV PATH="/app/venv/bin:$PATH"

COPY requirements.txt .

RUN pip install --no-cache-dir --timeout=300 -r requirements.txt

COPY . .

RUN mkdir -p tmp

ENV MONGODB_URI=mongodb+srv://atoovis:o7nzxQPS94tfBfJ5@cluster0.jtquyul.mongodb.net/atoovis_image_server
ENV CLOUDINARY_API_KEY=165794383214626
ENV CLOUDINARY_API_SECRET=0NCri36qtTDEwEHloUmaJ4tANEY
ENV CLOUDINARY_NAME=dqa43dbr9
ENV CACHE_SIZE=1000
ENV OUTPUT_QUALITY=95
ENV MAX_WORKERS=4
ENV TEMP_DIR=tmp
ENV KEEP_TEMP_FILES=False
ENV PORT=3000

EXPOSE 3000

CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 4 --log-level debug