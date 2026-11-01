FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py metadata.py extractor.py worker.py ./

ENV READING_DB_PATH=/data/reading.sqlite3

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]