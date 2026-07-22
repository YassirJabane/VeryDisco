# ==========================================
# STAGE 1: Build React Frontend with Vite
# ==========================================
FROM node:20-alpine AS frontend-builder
WORKDIR /build

# Copy frontend source files
COPY frontend/package*.json ./
RUN npm install

COPY frontend/ ./
RUN npm run build

# ==========================================
# STAGE 2: Python Backend Runtime environment
# ==========================================
FROM python:3.12-slim

# System setup
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV CONFIG_PATH=/app/config.yml
WORKDIR /app

# Install system dependencies (fpcalc for AcoustID)
RUN apt-get update && apt-get install -y --no-install-recommends libchromaprint-tools && rm -rf /var/lib/apt/lists/*

# Create a non-root user and group
RUN groupadd -g 1000 appgroup && \
    useradd -r -u 1000 -g appgroup appuser

# Pre-create required directory structures
RUN mkdir -p /data /app/frontend/dist /slskd_downloads /music && \
    chown -R appuser:appgroup /data /app /slskd_downloads /music

# Install Python backend dependencies (production only)
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy FastAPI backend code and scripts
COPY backend/app/ /app/backend/app/
COPY scripts/ /app/scripts/

# Copy React compiled assets from stage 1
COPY --from=frontend-builder /build/dist/ /app/frontend/dist/

# Ensure all application files are owned by the non-root user
RUN chown -R appuser:appgroup /app

# Switch to non-root user execution
USER appuser

# Expose port
EXPOSE 8080

# Health check — generous start-period for cold boot
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"

# Start Uvicorn serving FastAPI
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8080"]
