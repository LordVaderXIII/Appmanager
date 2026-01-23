from fastapi import FastAPI, Request, Depends, Form, HTTPException, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler
import uvicorn
import logging
import os
import hashlib
import json
from typing import List, Optional

from .database import engine, Base, get_db
from .models import Repository, Settings, ErrorLog
from .services.git_service import GitService
from .services.docker_service import DockerService
from .services.jules_service import JulesService

# Initialize Database
Base.metadata.create_all(bind=engine)

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AppManager")

# Ensure Data Directories
DATA_DIR = os.getenv("DATA_DIR", "./data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

def run_migrations():
    """
    Simple migration to ensure new columns exist in settings table.
    """
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            # Check if github_username column exists
            try:
                # SQLite specific check
                result = conn.execute(text("PRAGMA table_info(settings)"))
                columns = [row[1] for row in result.fetchall()]

                if "github_username" not in columns:
                    logger.info("Migrating DB: Adding github_username to settings")
                    conn.execute(text("ALTER TABLE settings ADD COLUMN github_username VARCHAR"))

                if "github_token" not in columns:
                    logger.info("Migrating DB: Adding github_token to settings")
                    conn.execute(text("ALTER TABLE settings ADD COLUMN github_token VARCHAR"))

                # Check for new columns in repositories
                result = conn.execute(text("PRAGMA table_info(repositories)"))
                repo_columns = [row[1] for row in result.fetchall()]

                if "container_name" not in repo_columns:
                    logger.info("Migrating DB: Adding container_name to repositories")
                    conn.execute(text("ALTER TABLE repositories ADD COLUMN container_name VARCHAR"))

                if "port_mappings" not in repo_columns:
                    logger.info("Migrating DB: Adding port_mappings to repositories")
                    conn.execute(text("ALTER TABLE repositories ADD COLUMN port_mappings TEXT"))

                if "volume_mappings" not in repo_columns:
                    logger.info("Migrating DB: Adding volume_mappings to repositories")
                    conn.execute(text("ALTER TABLE repositories ADD COLUMN volume_mappings TEXT"))

                if "env_vars" not in repo_columns:
                    logger.info("Migrating DB: Adding env_vars to repositories")
                    conn.execute(text("ALTER TABLE repositories ADD COLUMN env_vars TEXT"))

                conn.commit()
            except Exception as e:
                logger.warning(f"Migration check failed: {e}")
    except Exception as e:
        logger.error(f"Failed to connect for migration: {e}")

run_migrations()

app = FastAPI(title="App Manager")

# Templates
templates = Jinja2Templates(directory="src/templates")

# Scheduler
scheduler = BackgroundScheduler()

# Global Services
docker_service = DockerService()

def get_settings(db: Session):
    settings = db.query(Settings).first()
    if not settings:
        settings = Settings(jules_api_key="")
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings

# --- Job Logic ---

def check_and_run_repos():
    """
    Scheduled job to iterate over repositories, pull, build, run, and report errors.
    """
    db = next(get_db())
    repos = db.query(Repository).all()
    settings = get_settings(db)

    logger.info(f"Starting scheduled check for {len(repos)} repositories.")

    for repo in repos:
        try:
            process_repo(repo, db, settings.jules_api_key)
        except Exception as e:
            logger.error(f"Unexpected error processing {repo.name}: {e}")

    db.close()

def process_repo(repo: Repository, db: Session, api_key: str):
    # Retrieve settings for Git Auth
    settings = get_settings(db)

    # 1. Determine Local Path
    if not repo.local_path:
        repo_slug = repo.url.split("/")[-1].replace(".git", "")
        repo.local_path = os.path.join(os.getenv("DATA_DIR", "./data"), "repos", repo_slug)
        repo.name = "/".join(repo.url.split("/")[-2:]).replace(".git", "")
        db.commit()

    # 2. Clone or Pull
    repo_updated = False
    if not os.path.exists(repo.local_path):
        logger.info(f"Cloning {repo.name}...")
        success, msg = GitService.clone_repo(
            repo.url,
            repo.local_path,
            settings.github_username,
            settings.github_token
        )
        if not success:
            handle_error(repo, db, api_key, "Git Clone Error", msg)
            return
        repo_updated = True
    else:
        logger.info(f"Pulling {repo.name}...")
        success, msg = GitService.pull_repo(
            repo.local_path,
            repo.url,
            settings.github_username,
            settings.github_token
        )
        if not success and msg != "No updates":
             handle_error(repo, db, api_key, "Git Pull Error", msg)
             return
        if success:
            repo_updated = True

    # 3. Build and Run (if updated or previously failed/pending)
    # We also want to check if it's running? For now, we rebuild on update.
    if repo_updated or repo.status in ["pending", "error"]:
        repo.status = "building"
        db.commit()

        logger.info(f"Building/Running {repo.name}...")

        # Load Config from DB
        ports = json.loads(repo.port_mappings) if repo.port_mappings else None
        volumes = json.loads(repo.volume_mappings) if repo.volume_mappings else None
        env = json.loads(repo.env_vars) if repo.env_vars else None
        container_name = repo.container_name

        log_file = os.path.join(LOGS_DIR, f"{repo.id}.log")

        success, msg = docker_service.build_and_run(
            repo.local_path,
            repo.name,
            ports=ports,
            volumes=volumes,
            env=env,
            container_name=container_name,
            log_filepath=log_file,
            timeout=300
        )

        if not success:
            handle_error(repo, db, api_key, "Build/Run Error", msg)
            return
        else:
            repo.status = "active"
            repo.last_error_hash = None # Clear error state
            db.commit()

    # 4. Check Runtime Health (Logs)
    # Even if build succeeded, we check logs for immediate crashes or errors
    # This is a bit heuristic.
    logs = docker_service.get_logs(repo.local_path, repo.name, repo.container_name)
    # Simple heuristic: Check if container is running (handled by build_and_run somewhat)
    # If we wanted to scan logs for "Exception" or "Error", we could do it here.
    # For now, we rely on build/run exit codes mostly, but if the user wants to log
    # runtime errors caught by simple string matching:
    if "Traceback" in logs or "Error:" in logs or "Exception" in logs:
        # It might be a runtime error
        # Use a tail of logs to report
        error_snippet = logs[-3000:]
        handle_error(repo, db, api_key, "Runtime Error", error_snippet)

def handle_error(repo: Repository, db: Session, api_key: str, context: str, details: str):
    """
    Logs error, checks for duplicates, and reports to Jules.
    """
    repo.status = "error"
    full_error_text = f"{context}:\n{details}"

    # Generate Hash
    error_hash = hashlib.md5(full_error_text.encode("utf-8")).hexdigest()

    # Check if duplicate (same hash as last reported for this repo)
    if repo.last_error_hash == error_hash:
        logger.info(f"Skipping duplicate error for {repo.name}")
        db.commit()
        return

    # Log to DB
    error_log = ErrorLog(
        repository_id=repo.id,
        error_hash=error_hash,
        error_message=full_error_text
    )
    db.add(error_log)

    # Update Repo
    repo.last_error_hash = error_hash
    db.commit()

    # Report to Jules
    logger.info(f"Reporting new error for {repo.name} to Jules...")
    success, jules_msg = JulesService.report_error(api_key, repo.url, repo.name, full_error_text)
    if success:
        logger.info(f"Jules Session Created: {jules_msg}")
    else:
        logger.error(f"Failed to report to Jules: {jules_msg}")


# --- Lifecycle Events ---

@app.on_event("startup")
def startup_event():
    scheduler.add_job(check_and_run_repos, 'interval', minutes=5)
    scheduler.start()
    logger.info("Scheduler started.")

@app.on_event("shutdown")
def shutdown_event():
    scheduler.shutdown()

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    repos = db.query(Repository).all()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "repos": repos
    })

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    settings = get_settings(db)
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "api_key": settings.jules_api_key,
        "github_username": settings.github_username,
        "github_token": settings.github_token
    })

@app.post("/settings")
def update_settings(
    api_key: str = Form(""),
    github_username: str = Form(""),
    github_token: str = Form(""),
    db: Session = Depends(get_db)
):
    settings = get_settings(db)
    settings.jules_api_key = api_key
    settings.github_username = github_username
    settings.github_token = github_token
    db.commit()
    return RedirectResponse(url="/settings", status_code=303)

@app.post("/repos")
def add_repo(
    url: str = Form(...),
    link_container_id: Optional[str] = Form(None),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    if not url.endswith(".git"):
        # Helper to ensure it ends in .git for standard cloning
        pass

    existing = db.query(Repository).filter(Repository.url == url).first()
    if existing:
        raise HTTPException(status_code=400, detail="Repository already exists")

    new_repo = Repository(url=url, status="pending")

    if link_container_id:
        # Link Existing Container Logic
        config = docker_service.inspect_container(link_container_id)
        if config:
            new_repo.container_name = config["name"]
            new_repo.port_mappings = json.dumps(config["ports"])
            new_repo.volume_mappings = json.dumps(config["volumes"])
            new_repo.env_vars = json.dumps(config["env"])
            logger.info(f"Adopted config from container {config['name']}")
            new_repo.status = "active" # Don't rebuild immediately
    else:
        # New App Logic - Auto Configuration
        # Derive name from URL
        repo_slug = url.split("/")[-1].replace(".git", "")
        # Check if container name exists? simple heuristic for now.
        new_repo.container_name = repo_slug # Default to repo slug

        # Auto-Ports
        # Assuming internal port is 80 (common) or we just map 80/tcp to something?
        # Without knowing the internal port, we can't map it effectively unless we parse Dockerfile.
        # But for many apps, we assume some standard or let user configure later.
        # However, requirement says "intelligently selected".
        # If we don't know the internal port, we can't map it.
        # But if we assume 80 or 8080 or look at EXPOSE in Dockerfile later?
        # For now, let's just pick a free port and map to 80 as a guess, or
        # leave it empty and let Dockerfile EXPOSE handle it if using -P?
        # Better: Pick a free port, say 8090, and map 8090->80.
        free_port = docker_service.find_available_port()
        if free_port:
             new_repo.port_mappings = json.dumps({"80/tcp": free_port})

        # Auto-Volumes
        # /mnt/user/appdata/<name>
        host_path = f"/mnt/user/appdata/{repo_slug}"
        new_repo.volume_mappings = json.dumps({
            host_path: {"bind": "/config", "mode": "rw"} # Common convention for /config
        })

    db.add(new_repo)
    db.commit()

    # Trigger an immediate check in background
    background_tasks.add_task(check_and_run_repos)

    return RedirectResponse(url="/", status_code=303)

@app.post("/repos/{repo_id}/delete")
def delete_repo(repo_id: int, db: Session = Depends(get_db)):
    repo = db.query(Repository).filter(Repository.id == repo_id).first()
    if repo:
        # Optional: Delete local files?
        # shutil.rmtree(repo.local_path) if exists
        db.delete(repo)
        db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/repos/trigger")
def trigger_now(background_tasks: BackgroundTasks):
    background_tasks.add_task(check_and_run_repos)
    return RedirectResponse(url="/", status_code=303)

# --- Docker Endpoints ---

@app.get("/docker/containers")
def list_containers(db: Session = Depends(get_db)):
    # Get managed container names
    managed_repos = db.query(Repository).all()
    managed_names = [r.container_name for r in managed_repos if r.container_name]

    # Filter out managed ones
    containers = docker_service.list_containers(filter_names=managed_names)
    return JSONResponse(content=containers)

@app.get("/docker/containers/{container_id}")
def inspect_container(container_id: str):
    config = docker_service.inspect_container(container_id)
    if not config:
        raise HTTPException(status_code=404, detail="Container not found")
    return JSONResponse(content=config)

@app.get("/repos/{repo_id}/logs/build")
def get_build_logs(repo_id: int):
    log_file = os.path.join(LOGS_DIR, f"{repo_id}.log")
    if os.path.exists(log_file):
        with open(log_file, "r") as f:
            content = f.read()
        return PlainTextResponse(content)
    return PlainTextResponse("No logs found.", status_code=404)
