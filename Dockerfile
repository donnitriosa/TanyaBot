FROM python:3.11-slim

WORKDIR /app

# Install dependency
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Run as non-root user
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

# Jalankan bot
CMD ["python", "bot.py"]
