FROM python:3.11-slim

RUN pip install fastapi uvicorn aiohttp redis

WORKDIR /app

COPY health.py /app/health.py
COPY static /app/static

EXPOSE 9000

CMD ["uvicorn", "health:app", "--host", "0.0.0.0", "--port", "9000"]
