FROM python:3.12-slim

WORKDIR /app

# Copy shared library first (changes rarely — better layer caching)
COPY shared/ ./shared/

# Install API dependencies
COPY services/api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy API service code
COPY services/api/ ./

ENV PYTHONPATH=/app
ENV PORT=8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
