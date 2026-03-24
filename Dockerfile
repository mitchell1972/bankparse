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

# Create non-root user
RUN groupadd -g 1000 bankparse && \
    useradd -u 1000 -g bankparse -m bankparse && \
    chown -R bankparse:bankparse /app

USER bankparse

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-8000}/')" || exit 1

# Railway uses $PORT env var
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}
