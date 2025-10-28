# Use an official lightweight Python image.
# Using a specific version ensures consistency. Check Python compatibility if needed.
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Set environment variables to prevent Python from writing pyc files and keep output unbuffered
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies that might be needed by some Python packages
# (e.g., cryptography often needs build-essential, libffi-dev)
# If the build fails later on package installation, add required system packages here.
# RUN apt-get update && apt-get install -y --no-install-recommends build-essential libffi-dev && rm -rf /var/lib/apt/lists/*
# Note: The above RUN command is commented out; uncomment and adjust if needed.

# Copy the requirements file into the container
COPY requirements.txt .

# Install Python dependencies specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code (app.py, templates folder, etc.) into the container
COPY . .

# Expose the port that Gunicorn will run on (must match Gunicorn command)
EXPOSE 8080

# Define the command to run your application using Gunicorn and Uvicorn workers
# This is the command Cloud Run will execute to start your service.
# It binds to 0.0.0.0 (all interfaces) and port 8080.
# It uses the 'asgi_app' object from your app.py file.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--worker-class", "uvicorn.workers.UvicornWorker", "app:asgi_app"]

