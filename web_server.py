"""
Flow Reel Recreation Pipeline - WebUI Server
FastAPI backend providing a visual dashboard to browse old/new projects,
inspect scene transcriptions and generated clips, trigger human-in-the-loop review,
and configure & launch new video recreation runs.
"""

from pathlib import Path
import asyncio
import csv
import datetime
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.config import (
    DEFAULT_SCENE_THRESHOLD,
    DEFAULT_COOLDOWN_SEC,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_VIDEO_MODEL,
    FLOW_STUDIO_URL,
    get_flow_token,
    normalize_video_model,
    normalize_image_model,
)
from src.flow_client import FlowClient, load_job_state, save_job_state
from src.editor import run_stage_6
from src.review import regenerate_clip
from src.captions import (
    COLOR_PALETTES,
    POSITIONS,
    extract_words_from_whisper,
    generate_pop_subtitles_ass,
    burn_pop_captions_into_video,
)

logger = logging.getLogger("flow_reel.web")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

BASE_DIR = Path(__file__).resolve().parent
JOBS_DIR = BASE_DIR / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR = BASE_DIR / "web_static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Flow Reel Recreation Studio", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active pipeline background jobs {job_id: {process, thread, logs, status}}
ACTIVE_JOBS: dict[str, dict] = {}


# =============================================================================
# Helper Utilities
# =============================================================================

def get_job_summary(job_dir: Path) -> dict:
    job_id = job_dir.name
    state_file = job_dir / "state.json"
    scenes_csv = job_dir / "scenes.csv"
    prompts_file = job_dir / "prompts.json"
    transcript_file = job_dir / "transcript.json"
    final_video = job_dir / "final" / "final_reel.mp4"
    final_captioned = job_dir / "final" / "final_reel_captioned.mp4"

    # Creation time
    stat = job_dir.stat()
    created_at = datetime.datetime.fromtimestamp(stat.st_ctime).isoformat()

    # Detected scenes count
    scenes_count = 0
    if scenes_csv.exists():
        try:
            with open(scenes_csv, "r", encoding="utf-8") as f:
                scenes_count = max(0, len(list(csv.reader(f))) - 1)
        except Exception:
            pass

    # Completed clips count
    clips_dir = job_dir / "clips"
    completed_clips = len(list(clips_dir.glob("*.mp4"))) if clips_dir.exists() else 0

    # Status detection
    active_info = ACTIVE_JOBS.get(job_id)
    if active_info and active_info.get("status") == "running":
        status = "running"
    elif final_video.exists():
        status = "completed"
    elif completed_clips > 0 and completed_clips >= scenes_count and scenes_count > 0:
        status = "review_ready"
    elif completed_clips > 0:
        status = "generating"
    elif prompts_file.exists():
        status = "prompts_ready"
    elif scenes_count > 0:
        status = "scenes_ready"
    elif transcript_file.exists():
        status = "transcribed"
    else:
        status = "initialized"

    # Source info from transcript or state
    source_url = ""
    duration = 0.0
    if transcript_file.exists():
        try:
            with open(transcript_file, "r", encoding="utf-8") as f:
                t_data = json.load(f)
                duration = t_data.get("duration", 0.0)
        except Exception:
            pass

    # Thumbnail preview (first scene or generated image or video frame)
    thumbnail = None
    gen_images = list((job_dir / "generated_images").glob("*.png")) if (job_dir / "generated_images").exists() else []
    scene_shots = list((job_dir / "scenes").glob("*.jpg")) if (job_dir / "scenes").exists() else []
    if gen_images:
        thumbnail = f"/api/media/{job_id}/generated_images/{gen_images[0].name}"
    elif scene_shots:
        thumbnail = f"/api/media/{job_id}/scenes/{scene_shots[0].name}"

    return {
        "job_id": job_id,
        "created_at": created_at,
        "status": status,
        "scenes_count": scenes_count,
        "completed_clips": completed_clips,
        "duration": duration,
        "thumbnail": thumbnail,
        "has_final": final_video.exists(),
        "has_captioned": final_captioned.exists(),
    }


def stream_pipeline_process(job_id: str, cmd: list[str]):
    ACTIVE_JOBS[job_id] = {
        "status": "running",
        "logs": [],
        "started_at": datetime.datetime.now().isoformat(),
    }
    
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )
    ACTIVE_JOBS[job_id]["process"] = proc

    def reader():
        try:
            for line in proc.stdout:
                line_str = line.rstrip()
                ACTIVE_JOBS[job_id]["logs"].append(line_str)
                # Keep last 500 lines
                if len(ACTIVE_JOBS[job_id]["logs"]) > 500:
                    ACTIVE_JOBS[job_id]["logs"].pop(0)
            proc.wait()
            ACTIVE_JOBS[job_id]["status"] = "finished" if proc.returncode == 0 else "failed"
            ACTIVE_JOBS[job_id]["exit_code"] = proc.returncode
        except Exception as e:
            ACTIVE_JOBS[job_id]["status"] = "error"
            ACTIVE_JOBS[job_id]["error"] = str(e)

    thread = threading.Thread(target=reader, daemon=True)
    ACTIVE_JOBS[job_id]["thread"] = thread
    thread.start()


# =============================================================================
# API Endpoints
# =============================================================================

@app.get("/api/status")
def get_flow_status():
    """Checks Google Flow MCP server health and remaining credits."""
    try:
        client = FlowClient()
        status = client.check_status()
        flow_info = status.get("flow", {})
        return {
            "connected": True,
            "signedIn": flow_info.get("signedIn", False),
            "inProject": flow_info.get("inProject", False),
            "plan": flow_info.get("plan", "Unknown"),
            "credits_remaining": flow_info.get("credits_remaining", 0),
            "url": flow_info.get("url", ""),
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e),
            "signedIn": False,
            "credits_remaining": 0,
        }


@app.get("/api/avatars")
def list_available_avatars():
    """Lists avatar image files available in workspace."""
    results = []
    # Check root workspace and uploads
    for p in BASE_DIR.glob("avatar*.*"):
        if p.is_file() and p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
            results.append({
                "name": p.name,
                "path": str(p.resolve()),
                "url": f"/api/avatar_preview/{p.name}",
            })
    uploads_dir = BASE_DIR / "uploads"
    if uploads_dir.exists():
        for p in uploads_dir.glob("*.*"):
            if p.is_file() and p.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
                results.append({
                    "name": p.name,
                    "path": str(p.resolve()),
                    "url": f"/api/avatar_preview/uploads/{p.name}",
                })
    return results


@app.get("/api/avatar_preview/{filename:path}")
def serve_avatar(filename: str):
    p = BASE_DIR / filename
    if p.exists() and p.is_file():
        return FileResponse(p)
    raise HTTPException(status_code=404, detail="Avatar not found")


@app.post("/api/upload/avatar")
async def upload_avatar(file: UploadFile = File(...)):
    """Uploads a new avatar image into the workspace."""
    uploads_dir = BASE_DIR / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest_path = uploads_dir / file.filename
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {
        "name": file.filename,
        "path": str(dest_path.resolve()),
        "url": f"/api/avatar_preview/uploads/{file.filename}",
    }


@app.get("/api/projects")
def list_projects():
    """Lists all recreation pipeline projects with their current status and metadata."""
    projects = []
    if JOBS_DIR.exists():
        for job_dir in sorted(JOBS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if job_dir.is_dir() and job_dir.name.startswith("job_"):
                projects.append(get_job_summary(job_dir))
    return projects


@app.get("/api/projects/{job_id}")
def get_project_details(job_id: str):
    """Returns full project details: scenes, prompts, generated clips, review status, and edit paths."""
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists() or not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Job not found")

    summary = get_job_summary(job_dir)

    # 1. Prompts & Scenes mapping
    prompts_data = []
    prompts_file = job_dir / "prompts.json"
    if prompts_file.exists():
        try:
            with open(prompts_file, "r", encoding="utf-8") as f:
                prompts_data = json.load(f)
        except Exception:
            pass

    # 2. State & Completed Clips
    state_file = job_dir / "state.json"
    state_data = load_job_state(state_file)

    scenes_list = []
    clips_dir = job_dir / "clips"
    images_dir = job_dir / "generated_images"

    for p in prompts_data:
        num = p.get("scene_num", 1)
        sc_id = p.get("scene_id", f"scene_{num:02d}")
        screenshot_file = Path(p.get("screenshot_path", ""))
        
        img_name = f"img_{num:02d}.png"
        clip_name = f"clip_{num:02d}.mp4"

        has_image = (images_dir / img_name).exists()
        has_clip = (clips_dir / clip_name).exists()

        scenes_list.append({
            "scene_id": sc_id,
            "scene_num": num,
            "start_time": p.get("start_time", 0.0),
            "end_time": p.get("end_time", 0.0),
            "duration": p.get("original_duration", 0.0),
            "omni_duration": p.get("omni_duration", 4),
            "dialogue": p.get("dialogue", ""),
            "image_prompt": p.get("image_prompt", ""),
            "video_prompt": p.get("video_prompt", ""),
            "screenshot_url": f"/api/media/{job_id}/scenes/{screenshot_file.name}" if screenshot_file.exists() else None,
            "generated_image_url": f"/api/media/{job_id}/generated_images/{img_name}" if has_image else None,
            "clip_url": f"/api/media/{job_id}/clips/{clip_name}" if has_clip else None,
            "has_image": has_image,
            "has_clip": has_clip,
        })

    # 3. Final Outputs
    final_dir = job_dir / "final"
    final_video_url = f"/api/media/{job_id}/final/final_reel.mp4" if (final_dir / "final_reel.mp4").exists() else None
    
    captioned_name = None
    if (final_dir / "final_reel_pop_captioned.mp4").exists():
        captioned_name = "final_reel_pop_captioned.mp4"
    elif (final_dir / "final_reel_captioned.mp4").exists():
        captioned_name = "final_reel_captioned.mp4"

    final_captioned_url = f"/api/media/{job_id}/final/{captioned_name}" if captioned_name else None
    
    srt_text = ""
    srt_file = final_dir / "final_reel_pop_captioned.srt" if (final_dir / "final_reel_pop_captioned.srt").exists() else (final_dir / "final_reel.srt")
    if srt_file.exists():
        try:
            srt_text = srt_file.read_text(encoding="utf-8")
        except Exception:
            pass

    # 4. Source Video
    source_video_url = f"/api/media/{job_id}/source.mp4" if (job_dir / "source.mp4").exists() else None

    # 5. Live logs if active
    active_info = ACTIVE_JOBS.get(job_id, {})

    return {
        "summary": summary,
        "scenes": scenes_list,
        "source_video_url": source_video_url,
        "final_video_url": final_video_url,
        "final_captioned_url": final_captioned_url,
        "srt_text": srt_text,
        "is_active": active_info.get("status") == "running",
        "active_logs": active_info.get("logs", [])[-30:],
    }


@app.delete("/api/projects/{job_id}")
def delete_project(job_id: str):
    """Deletes a job directory and any running process."""
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    # Terminate process if running
    active = ACTIVE_JOBS.get(job_id)
    if active and "process" in active:
        try:
            active["process"].terminate()
        except Exception:
            pass

    shutil.rmtree(job_dir, ignore_errors=True)
    ACTIVE_JOBS.pop(job_id, None)
    return {"status": "deleted", "job_id": job_id}


class CreateProjectRequest(BaseModel):
    video_source: str
    avatar_path: str
    job_id: str | None = None
    threshold: float = DEFAULT_SCENE_THRESHOLD
    cooldown: float = DEFAULT_COOLDOWN_SEC
    model_image: str = DEFAULT_IMAGE_MODEL
    model_video: str = DEFAULT_VIDEO_MODEL
    burn_captions: bool = False
    caption_style: str = "yellow-pop"
    caption_position: str = "lower-third"
    mode: str = "stop_at_review"  # "dry_run", "stop_at_review", "full_auto"


@app.post("/api/projects/create")
def create_and_launch_project(req: CreateProjectRequest):
    """Creates a new job and launches the pipeline."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    source_hash = hashlib.md5(req.video_source.encode("utf-8")).hexdigest()[:6]
    job_id = req.job_id or f"job_{timestamp}_{source_hash}"
    job_dir = JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    # Build command
    cmd = [
        sys.executable,
        "-u",
        str(BASE_DIR / "run_reel.py"),
        req.video_source,
        "--avatar",
        req.avatar_path,
        "--job-id",
        job_id,
        "--threshold",
        str(req.threshold),
        "--cooldown",
        str(req.cooldown),
        "--model-image",
        normalize_image_model(req.model_image),
        "--model-video",
        normalize_video_model(req.model_video),
        "--caption-style",
        req.caption_style,
        "--caption-position",
        req.caption_position,
    ]

    if req.burn_captions:
        cmd.append("--burn-captions")

    if req.mode == "dry_run":
        cmd.append("--dry-run")
    elif req.mode == "stop_at_review":
        cmd.append("--stop-at-review")
    elif req.mode == "full_auto":
        cmd.append("--approve")

    stream_pipeline_process(job_id, cmd)
    return {"status": "started", "job_id": job_id}


class ProjectActionRequest(BaseModel):
    action: str  # "approve", "resume", "regen_clip"
    clip_num: int | None = None
    prompt_adjustment: str | None = None
    regen_image: bool = False
    burn_captions: bool = False
    caption_style: str = "yellow-pop"
    caption_position: str = "lower-third"


class BurnCaptionRequest(BaseModel):
    style: str = "yellow-pop"
    position: str = "lower-third"
    font_name: str = "Arial Black"
    font_size: int = 28
    words_per_screen: int = 3
    uppercase: bool = True
    model_size: str = "base"


@app.post("/api/projects/{job_id}/caption")
def burn_custom_captions(job_id: str, req: BurnCaptionRequest):
    """
    Stitches Flow clips if needed, transcribes speech with word timestamps,
    generates dynamic yellow-pop ASS subtitles, and burns into final_reel_pop_captioned.mp4.
    """
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    from caption_editor import process_job
    try:
        results = process_job(
            job_dir=job_dir,
            style=req.style,
            position=req.position,
            font_name=req.font_name,
            font_size=req.font_size,
            words_per_screen=req.words_per_screen,
            uppercase=req.uppercase,
            model_size=req.model_size,
        )
        return {
            "status": "success",
            "message": "Viral yellow-pop captions generated and burned successfully!",
            "final_captioned_url": f"/api/media/{job_id}/final/final_reel_pop_captioned.mp4",
            "final_video_url": f"/api/media/{job_id}/final/final_reel.mp4",
            "ass_url": f"/api/media/{job_id}/final/final_reel_pop_captioned.ass",
            "srt_url": f"/api/media/{job_id}/final/final_reel_pop_captioned.srt",
        }
    except Exception as e:
        logger.error(f"Caption burning failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/projects/{job_id}/action")
def perform_project_action(job_id: str, req: ProjectActionRequest, background_tasks: BackgroundTasks):
    """Handles Stage 5 review actions: approve & stitch, regenerate clip, or resume generation."""
    job_dir = JOBS_DIR / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="Job not found")

    prompts_file = job_dir / "prompts.json"
    if not prompts_file.exists():
        raise HTTPException(status_code=400, detail="Prompts not found for job")

    with open(prompts_file, "r", encoding="utf-8") as f:
        prompts_list = json.load(f)

    # 1. Action: Approve & Stitch (Stage 6)
    if req.action == "approve":
        logger.info(f"Triggering Stage 6 final edit for {job_id} (burn_captions={req.burn_captions}, style={req.caption_style})...")
        try:
            edit_res = run_stage_6(
                job_dir=job_dir,
                burn_captions=req.burn_captions,
                caption_style=req.caption_style,
                caption_position=req.caption_position,
            )
            return {
                "status": "success",
                "message": "Final edit concatenated and accurate SRT captions generated successfully!",
                "results": {
                    "final_reel": str(edit_res["final_reel"]),
                    "final_srt": str(edit_res["final_srt"]),
                    "final_captioned": str(edit_res.get("final_captioned", "")),
                },
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Stage 6 edit failed: {str(e)}")

    # 2. Action: Regenerate specific clip
    elif req.action == "regen_clip":
        if req.clip_num is None:
            raise HTTPException(status_code=400, detail="clip_num is required for regen_clip")

        # Find avatar
        avatar_path = None
        for p in prompts_list:
            if p.get("avatar_path") and Path(p["avatar_path"]).exists():
                avatar_path = Path(p["avatar_path"])
                break
        if not avatar_path:
            avatar_path = BASE_DIR / "avatar.jpg"

        def do_regen():
            regenerate_clip(
                scene_num=req.clip_num,
                prompt_adjustment=req.prompt_adjustment,
                job_dir=job_dir,
                job_id=job_id,
                prompts_list=prompts_list,
                avatar_path=avatar_path,
                regen_image=req.regen_image,
            )

        background_tasks.add_task(do_regen)
        return {
            "status": "started",
            "message": f"Regenerating clip {req.clip_num} in background (image={req.regen_image}).",
        }

    # 3. Action: Resume / Start Stage 4
    elif req.action == "resume":
        # Launch run_reel.py in stop-at-review mode
        avatar_path = BASE_DIR / "avatar.jpg"
        for p in prompts_list:
            if p.get("avatar_path") and Path(p["avatar_path"]).exists():
                avatar_path = Path(p["avatar_path"])
                break

        cmd = [
            sys.executable,
            "-u",
            str(BASE_DIR / "run_reel.py"),
            str(job_dir / "source.mp4"),
            "--avatar",
            str(avatar_path),
            "--job-id",
            job_id,
            "--stop-at-review",
        ]
        stream_pipeline_process(job_id, cmd)
        return {"status": "started", "message": f"Resumed generation for {job_id}."}

    raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")


@app.get("/api/projects/{job_id}/logs")
def get_job_logs(job_id: str):
    """Fetches live streaming logs for an active job."""
    active = ACTIVE_JOBS.get(job_id)
    if active:
        return {
            "status": active.get("status", "unknown"),
            "logs": active.get("logs", []),
        }
    return {"status": "idle", "logs": []}


@app.get("/api/media/{job_id}/{file_path:path}")
def get_media_file(job_id: str, file_path: str):
    """Serves media files (images, MP4 videos with HTTP 206 Range support) from job folder."""
    target_file = (JOBS_DIR / job_id / file_path).resolve()
    # Security check: ensure path stays within JOBS_DIR
    if not str(target_file).startswith(str(JOBS_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(target_file)


# =============================================================================
# Serve Frontend Single Page Application
# =============================================================================

@app.get("/")
def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Studio Frontend initializing...</h1>")
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"\n=======================================================")
    print(f"🎬 Flow Reel Recreation Studio WebUI")
    print(f"👉 Local URL: http://127.0.0.1:{port}")
    print(f"=======================================================\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
