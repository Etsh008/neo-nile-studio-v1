from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware


APP_VERSION = "1.0.0"
DATA_ROOT = Path(os.getenv("NEO_NILE_DATA_ROOT", "/workspace/neo-nile"))
PROJECTS_ROOT = DATA_ROOT / "projects"
EXPORTS_ROOT = DATA_ROOT / "exports"
DB_PATH = DATA_ROOT / "database" / "studio.db"
LOG_ROOT = DATA_ROOT / "logs"
ACE_BASE = f"http://127.0.0.1:{os.getenv('ACESTEP_API_PORT', '8001')}"
MODEL_NAME = os.getenv("ACESTEP_CONFIG_PATH", "acestep-v15-xl-turbo")
LM_MODEL = os.getenv("ACESTEP_LM_MODEL_PATH", "acestep-5Hz-lm-1.7B")
FAKE_ENGINE = os.getenv("NEO_NILE_FAKE_ENGINE", "false").lower() in {"1", "true", "yes"}

for folder in (PROJECTS_ROOT, EXPORTS_ROOT, DB_PATH.parent, LOG_ROOT):
    folder.mkdir(parents=True, exist_ok=True)


class BasicAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/api/health":
            return await call_next(request)

        expected_user = os.getenv("NEO_NILE_USER", "studio")
        expected_password = os.getenv("NEO_NILE_PASSWORD", "change-me-now")
        auth = request.headers.get("Authorization", "")

        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)
                if hmac.compare_digest(username, expected_user) and hmac.compare_digest(
                    password, expected_password
                ):
                    return await call_next(request)
            except Exception:
                pass

        return Response(
            "Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Neo Nile Studio"'},
        )


app = FastAPI(title="Neo Nile Studio V1", version=APP_VERSION)
app.add_middleware(BasicAuthMiddleware)

executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="neo-nile-worker")
engine_lock = threading.Lock()
engine_state: dict[str, Any] = {
    "api": "starting",
    "model": "waiting",
    "ready": False,
    "message": "Starting ACE-Step API…",
    "updated_at": None,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def init_database() -> None:
    with db_connect() as db:
        db.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                error TEXT,
                ace_task_id TEXT,
                result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );
            """
        )


init_database()


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    notes: str = Field(default="", max_length=1000)


class GenerationRequest(BaseModel):
    project_id: str
    title: str = Field(default="Untitled Track", min_length=1, max_length=120)
    prompt: str = Field(min_length=10, max_length=6000)
    lyrics: str = Field(default="", max_length=12000)
    instrumental: bool = True
    duration: int = Field(default=30, ge=10, le=600)
    bpm: int | None = Field(default=104, ge=30, le=300)
    key_scale: str = Field(default="")
    time_signature: str = Field(default="4")
    variations: int = Field(default=1, ge=1, le=4)
    auto_master: bool = True
    seed: int | None = None


def set_engine_state(**updates: Any) -> None:
    with engine_lock:
        engine_state.update(updates)
        engine_state["updated_at"] = utc_now()


def get_engine_state() -> dict[str, Any]:
    with engine_lock:
        return dict(engine_state)


def safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " _-." else "_" for ch in value)
    cleaned = " ".join(cleaned.split()).strip(" .")
    return cleaned[:120] or "track"


def update_job(job_id: str, **updates: Any) -> None:
    if not updates:
        return
    updates["updated_at"] = utc_now()
    columns = ", ".join(f"{key}=?" for key in updates)
    values = list(updates.values()) + [job_id]
    with db_connect() as db:
        db.execute(f"UPDATE jobs SET {columns} WHERE id=?", values)


def read_job(job_id: str) -> dict[str, Any]:
    with db_connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        raise KeyError(job_id)
    result = dict(row)
    result["settings"] = json.loads(result.pop("settings_json"))
    result["result"] = json.loads(result["result_json"]) if result.get("result_json") else None
    result.pop("result_json", None)
    return result


def wait_for_ace_api(timeout: int = 300) -> None:
    started = time.time()
    while time.time() - started < timeout:
        try:
            response = requests.get(f"{ACE_BASE}/health", timeout=5)
            if response.ok:
                set_engine_state(api="online", message="ACE-Step API online; loading models…")
                return
        except requests.RequestException:
            pass
        time.sleep(3)
    raise TimeoutError("ACE-Step API did not start within five minutes.")


def initialize_engine() -> None:
    if FAKE_ENGINE:
        set_engine_state(
            api="simulated",
            model="simulated",
            ready=True,
            message="Simulation engine ready.",
        )
        return

    try:
        wait_for_ace_api()
        set_engine_state(model="loading", message=f"Loading {MODEL_NAME} + {LM_MODEL}…")
        response = requests.post(
            f"{ACE_BASE}/v1/init",
            json={
                "model": MODEL_NAME,
                "slot": 1,
                "init_llm": True,
                "lm_model_path": LM_MODEL,
            },
            timeout=1800,
        )
        response.raise_for_status()
        wrapped = response.json()
        if wrapped.get("code") != 200:
            raise RuntimeError(wrapped.get("error") or str(wrapped))
        set_engine_state(
            model="ready",
            ready=True,
            message=f"{MODEL_NAME} and {LM_MODEL} are ready.",
        )
    except Exception as exc:
        set_engine_state(
            model="error",
            ready=False,
            message=f"Engine initialization failed: {exc}",
        )


@app.on_event("startup")
def on_startup() -> None:
    threading.Thread(target=initialize_engine, daemon=True, name="engine-initializer").start()


def wait_until_engine_ready(timeout: int = 1800) -> None:
    started = time.time()
    while time.time() - started < timeout:
        state = get_engine_state()
        if state["ready"]:
            return
        if state["model"] == "error":
            raise RuntimeError(state["message"])
        time.sleep(3)
    raise TimeoutError("The music engine did not become ready within 30 minutes.")


def create_fake_audio(output: Path, duration: int, bpm: int) -> None:
    import math
    import struct

    sample_rate = 48000
    total_frames = duration * sample_rate
    frequencies = (220.0, 329.63, 440.0)
    beat_period = max(1, int(sample_rate * 60 / max(bpm, 30)))

    with wave.open(str(output), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        for frame in range(total_frames):
            t = frame / sample_rate
            chord = sum(math.sin(2 * math.pi * f * t) for f in frequencies) / len(frequencies)
            pulse = 1.0 if frame % beat_period < sample_rate * 0.03 else 0.0
            value = max(-1.0, min(1.0, chord * 0.20 + pulse * 0.12))
            packed = struct.pack("<hh", int(value * 32767), int(value * 32767))
            wav.writeframesraw(packed)


def ffmpeg_run(arguments: list[str]) -> None:
    completed = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffmpeg failed")


def convert_to_wav(source: Path, target: Path) -> None:
    ffmpeg_run(["-i", str(source), "-c:a", "pcm_s24le", str(target)])


def master_audio(source: Path, target: Path) -> None:
    ffmpeg_run(
        [
            "-i",
            str(source),
            "-af",
            "highpass=f=25,loudnorm=I=-14:TP=-1.0:LRA=11",
            "-c:a",
            "pcm_s24le",
            str(target),
        ]
    )


def create_preview(source: Path, target: Path) -> None:
    ffmpeg_run(
        [
            "-t",
            "30",
            "-i",
            str(source),
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(target),
        ]
    )


def analyze_audio(source: Path) -> dict[str, Any]:
    duration = None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        duration = round(float(result.stdout.strip()), 2)
    except Exception:
        pass

    return {
        "duration": duration,
        "size_mb": round(source.stat().st_size / (1024 * 1024), 2),
        "format": source.suffix.lower().lstrip("."),
    }


def download_ace_file(reference: str, destination: Path) -> None:
    url = reference if reference.startswith(("http://", "https://")) else urljoin(ACE_BASE + "/", reference)
    with requests.get(url, stream=True, timeout=600) as response:
        response.raise_for_status()
        with destination.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)


def run_generation(job_id: str, request_data: dict[str, Any]) -> None:
    job_dir = PROJECTS_ROOT / request_data["project_id"] / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    try:
        update_job(job_id, status="waiting_engine", progress=5, message="Preparing the music engine…")
        wait_until_engine_ready()

        if FAKE_ENGINE:
            update_job(job_id, status="generating", progress=35, message="Generating simulation audio…")
            original = job_dir / "variation_1_original.wav"
            create_fake_audio(original, request_data["duration"], request_data.get("bpm") or 104)
            candidates = [
                {
                    "file_path": str(original),
                    "seed": request_data.get("seed") or 2026,
                    "prompt": request_data["prompt"],
                    "metas": {
                        "duration": request_data["duration"],
                        "bpm": request_data.get("bpm"),
                    },
                }
            ]
        else:
            update_job(job_id, status="submitting", progress=15, message="Submitting composition…")
            payload = {
                "prompt": request_data["prompt"],
                "lyrics": "" if request_data["instrumental"] else request_data.get("lyrics", ""),
                "thinking": True,
                "vocal_language": "en",
                "audio_format": "flac",
                "model": MODEL_NAME,
                "task_type": "text2music",
                "audio_duration": request_data["duration"],
                "bpm": request_data.get("bpm"),
                "key_scale": request_data.get("key_scale", ""),
                "time_signature": request_data.get("time_signature", "4"),
                "inference_steps": 8,
                "use_random_seed": request_data.get("seed") is None,
                "seed": request_data.get("seed", -1),
                "batch_size": request_data["variations"],
                "lm_model_path": LM_MODEL,
                "lm_backend": os.getenv("ACESTEP_LM_BACKEND", "vllm"),
                "use_cot_caption": True,
                "use_cot_language": False,
                "constrained_decoding": True,
            }
            response = requests.post(f"{ACE_BASE}/release_task", json=payload, timeout=60)
            response.raise_for_status()
            wrapped = response.json()
            if wrapped.get("code") != 200:
                raise RuntimeError(wrapped.get("error") or str(wrapped))
            ace_task_id = (wrapped.get("data") or {}).get("task_id")
            if not ace_task_id:
                raise RuntimeError("ACE-Step returned no task ID.")
            update_job(
                job_id,
                ace_task_id=ace_task_id,
                status="generating",
                progress=25,
                message="Composing and rendering lossless audio…",
            )

            started = time.time()
            while True:
                if time.time() - started > 1800:
                    raise TimeoutError("Generation exceeded 30 minutes.")
                query = requests.post(
                    f"{ACE_BASE}/query_result",
                    json={"task_id_list": [ace_task_id]},
                    timeout=30,
                )
                query.raise_for_status()
                query_data = query.json()
                entries = query_data.get("data") or []
                if entries:
                    entry = entries[0]
                    status = int(entry.get("status", 0))
                    if status == 2:
                        raise RuntimeError(entry.get("error") or entry.get("result") or "Generation failed.")
                    if status == 1:
                        raw_result = entry.get("result", [])
                        if isinstance(raw_result, str):
                            raw_result = json.loads(raw_result or "[]")
                        if isinstance(raw_result, dict):
                            raw_result = [raw_result]
                        candidates = list(raw_result)
                        break

                elapsed = int(time.time() - started)
                progress = min(78, 25 + elapsed // 5)
                update_job(
                    job_id,
                    progress=progress,
                    message=f"Generating music… {elapsed}s",
                )
                time.sleep(4)

            if not candidates:
                raise RuntimeError("Generation completed without audio files.")

            for index, candidate in enumerate(candidates, 1):
                source_reference = str(candidate.get("file", ""))
                extension = ".flac"
                original = job_dir / f"variation_{index}_original{extension}"
                download_ace_file(source_reference, original)
                candidate["file_path"] = str(original)

        update_job(job_id, status="post_processing", progress=82, message="Creating WAV and master files…")
        outputs = []
        for index, candidate in enumerate(candidates, 1):
            source = Path(candidate["file_path"])
            original_wav = job_dir / f"variation_{index}_original.wav"
            if source.suffix.lower() == ".wav":
                if source != original_wav:
                    shutil.copy2(source, original_wav)
            else:
                convert_to_wav(source, original_wav)

            master_wav = job_dir / f"variation_{index}_master.wav"
            if request_data.get("auto_master", True):
                master_audio(original_wav, master_wav)
            else:
                shutil.copy2(original_wav, master_wav)

            preview_mp3 = job_dir / f"variation_{index}_preview.mp3"
            create_preview(master_wav, preview_mp3)

            outputs.append(
                {
                    "variation": index,
                    "seed": candidate.get("seed_value") or candidate.get("seed"),
                    "original_url": f"/api/jobs/{job_id}/files/{original_wav.name}",
                    "master_url": f"/api/jobs/{job_id}/files/{master_wav.name}",
                    "preview_url": f"/api/jobs/{job_id}/files/{preview_mp3.name}",
                    "analysis": analyze_audio(master_wav),
                    "metas": candidate.get("metas", {}),
                }
            )

        manifest = {
            "job_id": job_id,
            "title": request_data["title"],
            "prompt": request_data["prompt"],
            "settings": request_data,
            "outputs": outputs,
            "engine": {
                "model": MODEL_NAME,
                "lm_model": LM_MODEL,
                "fake": FAKE_ENGINE,
            },
            "completed_at": utc_now(),
        }
        (job_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        update_job(
            job_id,
            status="completed",
            progress=100,
            message="Track completed.",
            result_json=json.dumps(manifest, ensure_ascii=False),
        )
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            message="Generation failed.",
            error=str(exc),
        )


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": APP_VERSION}


@app.get("/api/engine")
def engine() -> dict[str, Any]:
    return {
        **get_engine_state(),
        "model_name": MODEL_NAME,
        "lm_model": LM_MODEL,
        "fake_engine": FAKE_ENGINE,
    }


@app.post("/api/engine/retry")
def retry_engine() -> dict[str, Any]:
    current = get_engine_state()
    if current["model"] == "loading":
        return current
    set_engine_state(api="starting", model="waiting", ready=False, message="Retrying engine initialization…")
    threading.Thread(target=initialize_engine, daemon=True, name="engine-retry").start()
    return get_engine_state()


@app.get("/api/diagnostics")
def diagnostics() -> dict[str, Any]:
    gpu = {"available": False, "text": "nvidia-smi unavailable"}
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0:
            gpu = {"available": True, "text": result.stdout.strip()}
    except Exception as exc:
        gpu = {"available": False, "text": str(exc)}

    disk = shutil.disk_usage(DATA_ROOT)
    ace_health: dict[str, Any] = {"ok": False}
    try:
        response = requests.get(f"{ACE_BASE}/health", timeout=5)
        ace_health = {"ok": response.ok, "status": response.status_code}
    except Exception as exc:
        ace_health = {"ok": False, "error": str(exc)}

    return {
        "version": APP_VERSION,
        "engine": get_engine_state(),
        "gpu": gpu,
        "disk": {
            "total_gb": round(disk.total / 1024**3, 1),
            "used_gb": round(disk.used / 1024**3, 1),
            "free_gb": round(disk.free / 1024**3, 1),
        },
        "ace_api": ace_health,
        "paths": {
            "data_root": str(DATA_ROOT),
            "checkpoints": str(DATA_ROOT / "checkpoints"),
            "projects": str(PROJECTS_ROOT),
        },
    }


@app.get("/api/logs")
def logs() -> dict[str, str]:
    def tail(path: Path, max_lines: int = 160) -> str:
        if not path.exists():
            return ""
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-max_lines:])

    return {
        "ace_step": tail(LOG_ROOT / "ace-step.log"),
        "neo_nile": tail(LOG_ROOT / "neo-nile.log"),
    }


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    with db_connect() as db:
        rows = db.execute(
            """
            SELECT p.*,
                   COUNT(j.id) AS job_count,
                   SUM(CASE WHEN j.status='completed' THEN 1 ELSE 0 END) AS completed_count
            FROM projects p
            LEFT JOIN jobs j ON j.project_id = p.id
            GROUP BY p.id
            ORDER BY p.updated_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/projects")
def create_project(payload: ProjectCreate) -> dict[str, Any]:
    project_id = uuid.uuid4().hex
    now = utc_now()
    with db_connect() as db:
        db.execute(
            "INSERT INTO projects(id,name,notes,created_at,updated_at) VALUES(?,?,?,?,?)",
            (project_id, payload.name.strip(), payload.notes.strip(), now, now),
        )
    (PROJECTS_ROOT / project_id).mkdir(parents=True, exist_ok=True)
    return {
        "id": project_id,
        "name": payload.name.strip(),
        "notes": payload.notes.strip(),
        "created_at": now,
        "updated_at": now,
    }


@app.get("/api/jobs")
def list_jobs(project_id: str | None = None) -> list[dict[str, Any]]:
    with db_connect() as db:
        if project_id:
            rows = db.execute(
                "SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()

    results = []
    for row in rows:
        item = dict(row)
        item["settings"] = json.loads(item.pop("settings_json"))
        item["result"] = json.loads(item["result_json"]) if item.get("result_json") else None
        item.pop("result_json", None)
        results.append(item)
    return results


@app.post("/api/jobs")
def create_job(payload: GenerationRequest) -> dict[str, Any]:
    try:
        with db_connect() as db:
            project = db.execute("SELECT id FROM projects WHERE id=?", (payload.project_id,)).fetchone()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found.")

            job_id = uuid.uuid4().hex
            now = utc_now()
            settings = payload.model_dump()
            db.execute(
                """
                INSERT INTO jobs(
                    id,project_id,title,prompt,settings_json,status,progress,message,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    payload.project_id,
                    payload.title.strip(),
                    payload.prompt.strip(),
                    json.dumps(settings, ensure_ascii=False),
                    "queued",
                    0,
                    "Queued.",
                    now,
                    now,
                ),
            )
            db.execute(
                "UPDATE projects SET updated_at=? WHERE id=?",
                (now, payload.project_id),
            )
        executor.submit(run_generation, job_id, settings)
        return read_job(job_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    try:
        return read_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc


@app.get("/api/jobs/{job_id}/files/{filename}")
def job_file(job_id: str, filename: str) -> FileResponse:
    try:
        job = read_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found.") from exc

    job_dir = (PROJECTS_ROOT / job["project_id"] / job_id).resolve()
    path = (job_dir / filename).resolve()
    if job_dir not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    media_type = {
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".mp3": "audio/mpeg",
        ".json": "application/json",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name)


STATIC_ROOT = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="static")
