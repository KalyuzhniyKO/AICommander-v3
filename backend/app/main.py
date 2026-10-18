"""AICommander v3 foundation with opt-in Hermes planning transport."""
from __future__ import annotations
from contextlib import closing, asynccontextmanager
from pathlib import Path
from typing import Annotated
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
import sqlite3
from backend.app.security import secure_request, RedactedJSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from starlette.exceptions import HTTPException as StarletteHTTPException
from backend.app.config import get_settings, load_model_config, ModelConfigError, DEFAULT_MODELS_PATH
from backend.app.orchestration.round_runner import run_round, rerun_role
from backend.app.orchestration.model_check import check_models
from backend.app.storage.db import connect, init_db
from backend.app.storage.repositories import Repository
from backend.app.taskflow.api import create_router
from backend.app.localrun.panel import create_review_router
from backend.app.taskflow.hermes_api import create_hermes_router

@asynccontextmanager
async def lifespan(app):
    recover_interrupted()
    yield


app=FastAPI(lifespan=lifespan, default_response_class=RedactedJSONResponse, title="AICommander v3 Foundation", version="3.0.0-dev")
app.middleware("http")(secure_request)
settings=get_settings()


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return RedactedJSONResponse({"detail": jsonable_encoder(exc.errors())}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    return RedactedJSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


def repository():
    # sqlite3.Connection's context manager commits/rolls back, but does not close.
    with closing(connect(settings.database_path)) as conn:
        init_db(conn)
        yield Repository(conn)


RepositoryDependency = Annotated[Repository, Depends(repository)]

@app.get("/health")
def health():
    return {"status":"ok","foundation":True,"hermes_connected":False,"omnirouter_connected":False}

@app.post("/tasks")
def create_task(payload:dict, repo:RepositoryDependency):
    description=str(payload.get("description") or "").strip()
    if not description:
        raise HTTPException(status_code=400, detail="description required")
    project_id=payload.get("project_id", 1)
    if not isinstance(project_id, int) or not any(p["id"] == project_id for p in repo.list_projects()):
        raise HTTPException(status_code=400, detail="Неизвестный проект")
    return repo.create_task(description, project_id)

@app.get("/tasks/{task_id}")
def get_task(task_id:int, repo:RepositoryDependency):
    task=repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@app.post("/tasks/{task_id}/rounds")
def create_round(task_id:int,repo:RepositoryDependency,payload:dict|None=None):
    claim(task_id, repo)
    return execute(task_id, repo, settings, str((payload or {}).get("user_comment") or ""))

@app.post("/rounds/{round_id}/roles/{role}/rerun")
def rerun(round_id:int,role:str,repo:RepositoryDependency):
    rnd=repo.get_round(round_id)
    if not rnd:
        raise HTTPException(status_code=404, detail="Раунд не найден")
    if role not in {"manager", "architect", "designer", "coder", "reviewer"}:
        raise HTTPException(status_code=400, detail="Неизвестная роль")
    claim(rnd["task_id"], repo)
    return execute(rnd["task_id"], repo, settings, "", round_id, role)

@app.get("/models/status")
def model_status(repo:RepositoryDependency):
    stored=repo.list_model_status()
    try:
        configured=load_model_config()
        config_error=None
    except ModelConfigError as e:
        configured={}
        config_error=e.to_dict()
    keys={(m["role"],m["provider"],m["model_id"]) for m in stored}
    for role,refs in configured.items():
        for ref in refs:
            provider,mid=ref.split("/",1)
            if (role,provider,mid) not in keys:
                stored.append({"role":role,"provider":provider,"model_id":mid,"status":"unknown"})
    return {"models":stored,"config_file_exists":DEFAULT_MODELS_PATH.exists(),"config_error":config_error}

@app.post("/models/check")
def model_check(repo:RepositoryDependency):
    return check_models(settings,repo)



def claim(task_id, repo, comment=""):
    if repo.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    if not repo.transition(task_id, ("active", "awaiting_owner", "failed", "stopped", "rejected"), "running", "retry", comment):
        raise HTTPException(status_code=409, detail="Задача уже выполняется или подтверждена")


def execute(task_id, repo, worker_settings, comment="", round_id=None, role=None):
    try:
        try:
            models=load_model_config()
        except ModelConfigError:
            models={}
        result = (rerun_role(round_id, role, worker_settings, models, repo) if round_id
                  else run_round(task_id, comment, worker_settings, models, repo))
        outputs=result.get("role_outputs", [])
        success=bool(outputs) and all(o["status"] == "completed" for o in outputs)
        repo.transition(task_id, ("running",), "awaiting_owner" if success else "failed")
        return result
    except Exception:
        repo.transition(task_id, ("running",), "failed")
        raise
    finally:
        repo.transition(task_id, ("stopping",), "stopped")


def background_round(task_id, worker_settings, comment):
    with closing(connect(worker_settings.database_path)) as conn:
        init_db(conn)
        try:
            execute(task_id, Repository(conn), worker_settings, comment)
        except Exception:
            # Ошибка отражена состоянием failed, её текст не передаётся браузеру.
            pass


@app.get("/projects")
def projects(repo:RepositoryDependency):
    return repo.list_projects()


@app.post("/projects")
def add_project(payload:dict, repo:RepositoryDependency):
    name=str(payload.get("name") or "").strip()
    if not name or len(name)>120:
        raise HTTPException(status_code=400, detail="Название проекта: от 1 до 120 символов")
    try:
        return repo.create_project(name)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Проект уже существует")


@app.get("/tasks")
def tasks(repo:RepositoryDependency):
    return repo.list_tasks()


@app.get("/panel/status")
def panel_status(repo:RepositoryDependency):
    return {"execution": "offline_taskflow", "director": "mock", "simulated": True, "hermes_connected": False,
            "omnirouter_connected": False, "git_diff": None, "qa": None,
            "cost": {"available": False, "total": None, "reason": "Учёт затрат ожидает #18"},
            "models": [{k: m[k] for k in ("role", "provider", "model_id", "status", "response_time_ms")}
                       for m in repo.list_model_status()]}


@app.post("/tasks/{task_id}/run", status_code=202)
def start_task(task_id:int, background_tasks:BackgroundTasks, repo:RepositoryDependency, payload:dict|None=None):
    comment=str((payload or {}).get("comment") or "")[:4000]
    claim(task_id, repo, comment)
    background_tasks.add_task(background_round, task_id, settings, comment)
    return {"task_id": task_id, "status": "running"}


@app.post("/tasks/{task_id}/decision")
def decide(task_id:int, payload:dict, repo:RepositoryDependency):
    task=repo.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    action=payload.get("action")
    transitions={"approve": (("awaiting_owner",), "approved"),
                 "reject": (("active", "awaiting_owner", "failed", "stopped"), "rejected"),
                 "stop": (("running",), "stopping")}
    if not isinstance(action, str) or action not in transitions:
        raise HTTPException(status_code=400, detail="Неизвестное решение")
    states, target=transitions[action]
    if not repo.transition(task_id, states, target, action, str(payload.get("comment") or "")[:4000]):
        raise HTTPException(status_code=409, detail="Решение недоступно в текущем состоянии")
    return repo.get_task(task_id)


def recover_interrupted():
    with closing(connect(settings.database_path)) as conn:
        init_db(conn)
        conn.execute("UPDATE tasks SET status='failed', updated_at=CURRENT_TIMESTAMP WHERE status IN ('running','stopping')")
        conn.commit()

app.include_router(create_review_router(lambda: settings))
app.include_router(create_router(repository))
app.include_router(create_hermes_router(repository,lambda:settings))
app.mount("/",StaticFiles(directory=str(Path(__file__).resolve().parents[2] / "frontend"),html=True),name="frontend")

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="127.0.0.1",port=8000)
