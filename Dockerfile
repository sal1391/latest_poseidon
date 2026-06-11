#FROM python:3.11-slim

FROM wfscorp.jfrog.io/docker/enterprise-python-image:3.12.0@sha256:524433c009b8570ce80934b0f4f32bd4a81f7c051e76fe06272e9f8569df7bfa


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# WeasyPrint requires native libraries for Pango/Cairo rendering.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf-2.0-0 \
    libcairo2 \
    libffi-dev \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

EXPOSE 8501

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
