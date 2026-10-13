FROM python:3.10-slim

RUN apt-get update -qq && apt-get install -y -qq \
    libpq-dev gcc postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p uploads/products uploads/signatures uploads/receipts uploads/visits

EXPOSE 8000

ENTRYPOINT ["bash", "deploy/docker-entrypoint.sh"]
