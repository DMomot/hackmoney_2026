FROM python:3.13-slim

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
COPY relayer/requirements.txt /app/relayer/requirements.txt

RUN pip install --no-cache-dir -r backend/requirements.txt -r relayer/requirements.txt

COPY backend/ /app/backend/
COPY relayer/ /app/relayer/
COPY .env /app/.env

EXPOSE 8000

WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
