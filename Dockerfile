FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p .seed
COPY server/default_model_specs.json .seed/default_model_specs.json
COPY server/default_embedding_specs.json .seed/default_embedding_specs.json

COPY server ./server

EXPOSE 8000
CMD ["uvicorn", "server.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
