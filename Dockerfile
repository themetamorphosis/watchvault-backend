FROM python:3.11-slim

# Prevents Python from writing pyc files to disc
ENV PYTHONDONTWRITEBYTECODE=1
# Prevents Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get -y install libpq-dev gcc libmagic-dev && apt-get clean

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an unprivileged user. The uploads directory is created and chowned
# here because docker-compose mounts a volume over it, and the mount inherits
# this ownership — without it the app cannot write avatars.
RUN useradd --create-home --uid 10001 lumiere \
    && mkdir -p /app/uploads \
    && chown -R lumiere:lumiere /app
USER lumiere

# Liveness only: never touches the database or any third party, so restarting
# on a failed probe can't be triggered by a slow dependency.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=4).status == 200 else 1)"

# Expose port and start
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
