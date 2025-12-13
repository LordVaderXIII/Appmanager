# Agent Instructions (AGENTS.md)

This file contains instructions and context for AI agents working on this codebase.

## Project Scope
The App Manager is a system to manage the lifecycle of other Docker applications. It runs on the host (Unraid) and uses the host's Docker socket to spawn sibling containers.

## Technology Stack
- **Language**: Python 3.11+
- **Framework**: FastAPI
- **Database**: SQLite with SQLAlchemy ORM
- **Task Queue/Scheduling**: APScheduler
- **Docker Interaction**: `docker` Python SDK
- **Frontend**: Jinja2 Templates + Vanilla JS

## Architectural Guidelines

1.  **Docker-in-Docker (Siblings)**:
    -   We do *not* use true Docker-in-Docker (dind). We mount `/var/run/docker.sock`.
    -   All paths for volumes (e.g., source code) must be accessible to the Docker daemon. *Self-reflection: Since we are in a container, mapping a volume from our container to a sibling container is tricky. The standard approach for this tool is that we clone code into a volume that is shared or we rely on building images and not using bind mounts for the apps unless necessary. For this MVP, we will assume standard `COPY . .` in Dockerfiles for the apps we build.*

2.  **Error Handling**:
    -   Any interaction with the Jules API must be robust. If the API is down, log locally and retry later (or skip).
    -   "Duplicate Errors" are defined by hashing the error message content.

3.  **Directory Structure**:
    -   `src/`: Source code
    -   `src/main.py`: Entry point
    -   `src/models.py`: Database models
    -   `src/services/`: Business logic (Git, Docker, Jules)
    -   `data/`: Persistent storage for SQLite and cloned repos.

## Verification
-   Always verify that `docker-compose.yml` mounts the socket.
-   Ensure the `data` volume is writable.

## Code Style
-   Follow PEP 8.
-   Use type hints.
