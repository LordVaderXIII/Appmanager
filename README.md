# App Manager

App Manager is a Docker-based application designed to run on Unraid (or any Docker host) to monitor, build, and run GitHub-based applications. It automatically detects errors during build or runtime and reports them to the Jules API for automated fixing.

## Features

- **GitHub Monitoring**: Polls public and private GitHub repositories for updates every 5 minutes.
- **Automated Build & Run**: Detects `docker-compose.yml` or `Dockerfile` and builds/runs the application using the host's Docker engine.
- **Private Repo Support**: Supports GitHub Username and Personal Access Token (PAT) authentication.
- **Error Reporting**: Captures build and runtime errors.
- **Jules Integration**: Integrates with the Jules API to report unique errors and request fixes.
- **Web Interface**: A decent dashboard to manage repositories and configure settings.
- **Duplicate Error Suppression**: Ensures the same error is not reported multiple times.

## Installation & Deployment

### Prerequisites
- Docker
- Docker Compose
- Access to `/var/run/docker.sock`

### Running on Unraid / Docker Compose

1. Clone this repository.
2. Run the following command:

```bash
docker-compose up -d
```

3. Access the dashboard at `http://<your-server-ip>:8000`.

#### Docker Configuration Details

*   **Port:** `8000` (Mapped to host 8000 by default)
*   **Volumes:**
    *   `/var/run/docker.sock:/var/run/docker.sock` (Required for building sibling containers)
    *   `app-data:/app/data` (Persists database and cloned repositories)
*   **Environment Variables:**
    *   `PYTHONUNBUFFERED=1` (Standard Python logging)

## Configuration

### Initial Setup
1. Open the UI at `http://localhost:8000` (or your server IP).
2. Go to **Settings**.
3. **Jules API Key**: Enter your Jules API Key for error reporting.
4. **GitHub Authentication**:
    *   If you plan to monitor private repositories, enter your **GitHub Username** and **Personal Access Token (PAT)**.
    *   These credentials are used for *all* private repositories added to the system.
    *   The PAT needs `repo` scope access.

### Adding Repositories
1. Go to the **Dashboard**.
2. Enter the HTTPS clone URL of the repository (e.g., `https://github.com/user/my-app.git`).
3. Click **Add Repository**.
4. The system will clone, build, and run the repository automatically.

### Repository Auto-Configuration
When adding a new repository, App Manager attempts to read a `docker-compose.yml` (or `docker-compose.yaml`) file from the root of the repository. It parses the first service definition to pre-fill the configuration form with:
*   **Container Name**
*   **Ports**
*   **Volumes**
*   **Environment Variables**

If no compose file is found, it falls back to standard defaults (internal port 80, `/config` volume).

## Architecture

- **Backend**: Python (FastAPI)
- **Database**: SQLite (persisted in volume)
- **Frontend**: HTML/JavaScript (Jinja2 templates)
- **Task Scheduling**: APScheduler
