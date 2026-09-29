FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 RAILWAY_MODE=1 BROWSER_PROFILE_PATH=/tmp/amazon_browser_profile
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    python -m playwright install --with-deps --only-shell chromium && \
    apt-get update && apt-get install -y --no-install-recommends fonts-noto-core fonts-noto-color-emoji && \
    rm -rf /var/lib/apt/lists/* /root/.cache/pip
COPY Egypt_Melook_wafrcash_links.py egypt_offer_shortener.py ./
CMD ["python", "Egypt_Melook_wafrcash_links.py"]
