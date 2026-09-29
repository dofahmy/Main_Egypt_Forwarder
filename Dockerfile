FROM mcr.microsoft.com/playwright/python:v1.55.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    apt-get update && apt-get install -y --no-install-recommends fonts-noto-core fonts-noto-color-emoji && \
    rm -rf /var/lib/apt/lists/*
COPY . .
ENV PYTHONUNBUFFERED=1 RAILWAY_MODE=1 BROWSER_PROFILE_PATH=/tmp/amazon_browser_profile
CMD ["python", "Egypt_Melook_wafrcash_links.py"]
