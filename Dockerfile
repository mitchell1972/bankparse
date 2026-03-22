FROM python:3.12-slim

# Install Tesseract OCR + HEIF support
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libheif-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytesseract pillow-heif libsql-experimental anthropic

# Copy app
COPY . .

RUN mkdir -p uploads outputs

EXPOSE 8000

# Railway uses $PORT env var
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
