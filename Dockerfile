# Backend image: FastAPI + OCR + Gemini client. Build: docker build -t claim-settlement-backend .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Shared libraries OpenCV (used by RapidOCR) and ONNX Runtime need on a slim image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Load the OCR models once at build time, so the first upload in a container isn't slow.
RUN python -c "from rapidocr import RapidOCR; RapidOCR()"

COPY . .
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/uploads /app/evals/.cache \
    && chown -R app:app /app/uploads /app/evals
USER app

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
