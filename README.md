# App Manager

App Manager is a Docker-based application designed to run on Unraid (or any Docker host) to monitor, build, and run GitHub-based applications. It automatically detects errors during build or runtime and reports them to the Jules API for automated fixing.

## Features

- **GitHub Monitoring**: Polls public GitHub repositories for updates every 5 minutes.
- **Automated Build & Run**: Detects `docker-compose.yml` or `Dockerfile` and builds/runs the application using the host's Docker engine.
- **Error Reporting**: Captures build and runtime errors.
- **Jules Integration**: Integrates with the Jules API to report unique errors and request fixes.
- **Web Interface**: A simple dashboard to manage repositories and configure the Jules API key.
- **Duplicate Error Suppression**: Ensures the same error is not reported multiple times.

## Installation & Deployment

### Prerequisites
- Docker
- Docker Compose
- Access to `/var/run/docker.sock`

### Running on Unraid / Docker Compose

1. Clone this repository.
2. Ensure you have a valid Jules API Key.
3. Run the following command:

```bash
docker-compose up -d
```

4. Access the dashboard at `http://<your-server-ip>:8000`.

## Configuration

- **Jules API Key**: Set this in the Web UI settings.
- **Repositories**: Add public GitHub repository URLs in the Web UI.

## Architecture

- **Backend**: Python (FastAPI)
- **Database**: SQLite (persisted in volume)
- **Frontend**: HTML/JavaScript (Jinja2 templates)
- **Task Scheduling**: APScheduler
