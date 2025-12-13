FROM python:3.11-slim

WORKDIR /app

# Install git and dependencies for installing Docker CLI
RUN apt-get update && apt-get install -y \
    git \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    && rm -rf /var/lib/apt/lists/*

# Install Docker CLI and Docker Compose Plugin
RUN mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
    $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null && \
    apt-get update && \
    apt-get install -y docker-ce-cli docker-compose-plugin && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Create a directory for data persistence
RUN mkdir -p /app/data

# Environment variable to point to the persistent data directory
ENV DATA_DIR=/app/data

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
