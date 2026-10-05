FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    U2NET_HOME=/models

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the light model so startup is fast
RUN python -c "from rembg import new_session; new_session('u2netp')"

COPY bot.py .
CMD ["python", "bot.py"]
