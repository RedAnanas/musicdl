FROM docker.m.daocloud.io/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt "Flask>=3.0"

COPY . ./
RUN pip install --no-cache-dir .

EXPOSE 5000

CMD ["python", "-X", "utf8", "examples/claudeai-modern-web-music-player/app.py"]
