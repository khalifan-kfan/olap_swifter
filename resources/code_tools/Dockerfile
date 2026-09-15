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
    pip install --no-cache-dir duckdb pydantic pandas networkx pytesseract Pillow presidio-analyzer presidio-anonymizer typer rich

# Download spacy lightweight model for Presidio offline NLP
RUN python -m spacy download en_core_web_sm

# Copy source code
COPY src/ ./src/
COPY skills/ ./skills/

# Install local package in editable mode
RUN pip install --no-cache-dir -e .

ENTRYPOINT ["olap-swifter"]
CMD ["--help"]
