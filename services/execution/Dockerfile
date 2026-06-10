FROM python:3.12-slim

WORKDIR /app

# Copy shared library first (changes rarely — better layer caching)
COPY shared/ ./shared/

# Install execution dependencies
COPY services/execution/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy execution service code
COPY services/execution/ ./services/execution/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "services/execution/main.py"]
