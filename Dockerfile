FROM python:3.13-alpine

RUN apk add --no-cache ffmpeg

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir .

EXPOSE 9118

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9118"]
