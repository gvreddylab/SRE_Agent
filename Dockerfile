FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gcc g++ && \
    rm -rf /var/lib/apt/lists/*

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY app/ ./app/
COPY .env.example .env

# Data dirs
RUN mkdir -p data/sqlite data/chroma data/reports data/assets

# Expose ports
EXPOSE 8501 8000

# Start both FastAPI and Streamlit
COPY scripts/docker-entrypoint.sh ./entrypoint.sh
RUN chmod +x entrypoint.sh

CMD ["./entrypoint.sh"]
