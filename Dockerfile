FROM python:3.11-slim

WORKDIR /app

# Install dependency
RUN pip install --no-cache-dir discord.py httpx

# Copy source code
COPY bot.py .

# Jalankan bot
CMD ["python", "bot.py"]