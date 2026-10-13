# Lightweight container runner for tools requiring system binaries (Tesseract OCR, Presidio NLP)
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install system dependencies including Tesseract OCR & development libraries
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    libtesseract-dev \
    libgl1 \
    libglib2.0-0 \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency definition
COPY pyproject.toml .

# Install Python packages
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir duckdb pydantic pandas networkx PyYAML pytesseract Pillow presidio-analyzer presidio-anonymizer typer rich spacy && \
    python -m spacy download en_core_web_sm

# Copy package source code
COPY olap_swifter/ ./olap_swifter/
COPY ocr_tools/ ./ocr_tools/
COPY privacy_tools/ ./privacy_tools/

# Install local package in editable mode
RUN pip install --no-cache-dir -e .

CMD ["olap-swifter", "--help"]
