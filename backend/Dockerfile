FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py metadata.py extractor.py worker.py nas_temps.py wud.py hacker_news.py availability.py feeds.py saved_copies.py shortcuts.py dsm.py dsm_services.py document_uploads.py pdf_progress.py article_progress.py home.py weather.py shortcut_catalog.json shortcut_defaults.json ./

ENV READING_DB_PATH=/data/reading.sqlite3

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
