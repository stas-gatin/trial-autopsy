FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8000

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Node.js runs the local Meterless World Model service (services/meterless), started by run.py.
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && rm -rf /var/lib/apt/lists/*
COPY services/meterless/package.json services/meterless/package-lock.json services/meterless/
RUN npm --prefix services/meterless ci --no-fund --no-audit

COPY . .
RUN mkdir -p logs data/world data/runs data/cache/http data/meterless

EXPOSE 8000
CMD ["python", "run.py"]
