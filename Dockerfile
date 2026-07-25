FROM python:3.11-slim

WORKDIR /app

# Install dependency
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Jalankan bot
CMD ["python", "bot.py"]
