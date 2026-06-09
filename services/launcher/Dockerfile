FROM python:3.12-slim

WORKDIR /app

# Copy shared library first (changes rarely — better layer caching)
COPY shared/ ./shared/

# Install launcher dependencies
COPY services/launcher/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy launcher service code
COPY services/launcher/ ./

ENV PYTHONPATH=/app
ENV PORT=8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
