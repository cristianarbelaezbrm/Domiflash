# # Imagen ligera y estable
# FROM python:3.11-slim

# # Evita prompts interactivos y mejora logs
# ENV PYTHONDONTWRITEBYTECODE=1 \
#     PYTHONUNBUFFERED=1 \
#     PORT=8080

# WORKDIR /app

# # Dependencias del sistema (útiles para requests/ssl y builds)
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     ca-certificates \
#     && rm -rf /var/lib/apt/lists/*

# # Instala deps primero (mejor cache)
# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt

# # Copia el código
# COPY . .

# # Cloud Run escucha en $PORT
# EXPOSE 8080

# # Uvicorn recomendado para FastAPI
# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# System deps (compilación + TLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    gcc \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

# Si tu app está en app/main.py:
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips=*"]



