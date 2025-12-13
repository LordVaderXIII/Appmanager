from fastapi import FastAPI, Request, Depends, Form, HTTPException, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from apscheduler.schedulers.background import BackgroundScheduler
import uvicorn
import logging
import os
import hashlib
from typing import List

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
        success, msg = GitService.clone_repo(repo.url, repo.local_path)
        if not success:
            handle_error(repo, db, api_key, "Git Clone Error", msg)
            return
        repo_updated = True
    else:
        logger.info(f"Pulling {repo.name}...")
        success, msg = GitService.pull_repo(repo.local_path)
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
        success, msg = docker_service.build_and_run(repo.local_path, repo.name)

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
    logs = docker_service.get_logs(repo.local_path, repo.name)
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
    settings = get_settings(db)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "repos": repos,
        "api_key": settings.jules_api_key
    })

@app.post("/settings")
def update_settings(api_key: str = Form(...), db: Session = Depends(get_db)):
    settings = get_settings(db)
    settings.jules_api_key = api_key
    db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/repos")
def add_repo(url: str = Form(...), background_tasks: BackgroundTasks = None, db: Session = Depends(get_db)):
    if not url.endswith(".git"):
        # Helper to ensure it ends in .git for standard cloning, though git clone usually handles it.
        pass

    existing = db.query(Repository).filter(Repository.url == url).first()
    if existing:
        raise HTTPException(status_code=400, detail="Repository already exists")

    new_repo = Repository(url=url, status="pending")
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
