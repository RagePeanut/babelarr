FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lingarr ./lingarr

# Default config dir for an optional mounted subtitle_rules.yml.
VOLUME ["/config"]

ENTRYPOINT ["python", "-m", "lingarr"]
