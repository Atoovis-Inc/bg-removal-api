FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p tmp

ENV MONGODB_URI=mongodb://your-mongodb-atlas-uri  
ENV CLOUDINARY_API_KEY=165794383214626
ENV CLOUDINARY_API_SECRET=0NCri36qtTDEwEHloUmaJ4tANEY
ENV CLOUDINARY_NAME=dqa43dbr9
ENV CACHE_SIZE=1000
ENV OUTPUT_QUALITY=95
ENV MAX_WORKERS=4
ENV TEMP_DIR=/tmp/bg_removal
ENV KEEP_TEMP_FILES=False

EXPOSE 8000

CMD ["gunicorn", "--workers", "4", "--threads", "8", "--timeout", "0", "--bind", "0.0.0.0:8000", "app.main:app"]