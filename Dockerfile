FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY babelarr ./babelarr

# Default config dir for optional mounted *_rules.yml files.
VOLUME ["/config"]

ENTRYPOINT ["python", "-m", "babelarr"]
