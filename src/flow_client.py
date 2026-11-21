"""
Stage 4 — Google Flow Generation Client (via GTA1 MCP Flow):
Interacts with the Flow MCP server to generate Nano Banana Pro 2.1 images and OmniFlash 1.1 videos.
"""

from pathlib import Path
import json
import logging
import shutil
import time
import requests

from .config import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_VIDEO_MODEL,
    FLOW_STUDIO_URL,
    FLOW_OUTPUT_ROOT,
    get_flow_token,
    normalize_video_model,
    normalize_image_model,
)

logger = logging.getLogger("flow_reel.flow_client")


class FlowClient:
    """Client for driving Google Flow generation via the Flow MCP server API."""

    def __init__(self, base_url: str = FLOW_STUDIO_URL):
        self.base_url = base_url.rstrip("/")
        self.token = get_flow_token()
        self.headers = {
            "Content-Type": "application/json",
            "x-flow-token": self.token,
        }

    def _post(self, endpoint: str, payload: dict | None = None) -> dict:
        url = f"{self.base_url}/api/{endpoint.lstrip('/')}"
        resp = requests.post(url, json=payload or {}, headers=self.headers, timeout=120)
        if not resp.ok:
            try:
                err_data = resp.json()
                msg = err_data.get("error", resp.text)
            except Exception:
                msg = resp.text
            raise RuntimeError(f"Flow API error on {endpoint} (HTTP {resp.status_code}): {msg}")
        return resp.json()

    def check_status(self) -> dict:
        """Checks Flow session status. Relies on Flow Chrome session."""
        status = self._post("status", {"detailed": True})
        flow_info = status.get("flow", {})
        if not flow_info.get("signedIn"):
            raise RuntimeError(
                "Not signed in to Google Flow! Please log in to Google Flow in the Flow Chrome window."
            )
        if not flow_info.get("inProject"):
            logger.info("No open project in Flow. Creating a new project...")
            self.create_project("Recreation Pipeline")
            status = self._post("status", {"detailed": False})
        return status

    def create_project(self, title: str = "Recreation Pipeline") -> str:
        """Creates a new project in Flow and waits for it to open."""
        res = self._post("new_project", {"title": title})
        task_id = res.get("task_id")
        if task_id:
            logger.info(f"Waiting for new Flow project creation (task: {task_id})...")
            wait_res = self.wait_for_jobs(job_ids=[task_id], timeout_seconds=90)
            return task_id
        return res.get("id", "")

    def wait_for_jobs(
        self,
        job_ids: list[str] | None = None,
        timeout_seconds: int = 300,
        poll_interval: float = 3.0,
    ) -> dict:
        """Polls wait endpoint until finished is True and checks for errors."""
        deadline = time.time() + timeout_seconds
        last_resp = {}
        while time.time() < deadline:
            res = self._post("wait", {"job_ids": job_ids or [], "timeout_seconds": 30})
            last_resp = res

            # Check if any tasks failed
            for task in res.get("tasks", []):
                if task.get("status") == "failed":
                    raise RuntimeError(f"Flow task {task.get('task_id')} failed: {task.get('error')}")
            # Check if any jobs failed
            for job in res.get("jobs", []):
                if job.get("status") == "failed":
                    raise RuntimeError(f"Flow job {job.get('id')} failed: {job.get('error')}")

            if res.get("finished", False):
                return res

            time.sleep(poll_interval)
        return last_resp

    def generate_image_scene(
        self,
        project_name: str,
        prompt: str,
        avatar_path: Path,
        dest_image_path: Path,
        screenshot_path: Path | None = None,
        model: str = DEFAULT_IMAGE_MODEL,
        max_credits: int = 0,
    ) -> Path:
        """
        Pass 1: Generates an image using Nano Banana Pro 2.1 with avatar reference image
        and optional scene screenshot reference for props, food items, pose, and background matching.
        Downloads and saves to dest_image_path.
        """
        dest_image_path.parent.mkdir(parents=True, exist_ok=True)
        abs_avatar = str(avatar_path.resolve())

        reference_images = [abs_avatar]
        if screenshot_path:
            p_sc = Path(screenshot_path).resolve()
            if p_sc.is_file():
                reference_images.append(str(p_sc))

        target_model = normalize_image_model(model)
        scene_spec = {
            "type": "image",
            "model": target_model,
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "reference_images": reference_images,
            "download_quality": "upscaled",
            "max_credits": max_credits,
        }

        payload = {
            "project": project_name,
            "scenes": [scene_spec],
        }

        logger.info(f"Queueing image generation with model '{model}'...")
        res = self._post("generate", payload)
        jobs = res.get("jobs", [])
        if not jobs:
            raise RuntimeError(f"No jobs returned from flow_generate: {res}")

        job_id = jobs[0]["id"]
        logger.info(f"Queued image job: {job_id}. Waiting for completion...")
        wait_res = self.wait_for_jobs(job_ids=[job_id], timeout_seconds=300)

        # Locate the resulting file
        output_file = None
        for j in wait_res.get("jobs", []):
            if j.get("id") == job_id:
                files = j.get("files", [])
                if files:
                    output_file = Path(files[0])
                break

        if not output_file or not output_file.exists():
            # Search in project output dir
            proj_dir = FLOW_OUTPUT_ROOT / project_name
            candidates = list(proj_dir.glob("*.png")) + list(proj_dir.glob("*.jpg"))
            if candidates:
                output_file = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]

        if not output_file or not output_file.exists():
            raise FileNotFoundError(f"Could not find generated image file for job {job_id}")

        shutil.copy2(output_file, dest_image_path)
        logger.info(f"Generated image saved to {dest_image_path}")
        return dest_image_path

    def generate_video_scene(
        self,
        project_name: str,
        prompt: str,
        first_frame_path: Path,
        dest_video_path: Path,
        duration: int = 4,
        model: str = DEFAULT_VIDEO_MODEL,
        max_credits: int = 25,
    ) -> Path:
        """
        Pass 2: Generates a video clip using OmniFlash 1.1 starting from the image pass.
        Downloads at maximum quality (upscaled) into dest_video_path.
        """
        dest_video_path.parent.mkdir(parents=True, exist_ok=True)
        abs_first_frame = str(first_frame_path.resolve())

        target_model = normalize_video_model(model)
        scene_spec = {
            "type": "video",
            "model": target_model,
            "prompt": prompt,
            "aspect_ratio": "9:16",
            "first_frame": abs_first_frame,
            "duration": duration,
            "resolution": "720p",
            "download_quality": "upscaled",
            "max_credits": max_credits,
            "on_screen_text": False,
        }

        payload = {
            "project": project_name,
            "scenes": [scene_spec],
        }

        logger.info(
            f"Queueing video generation with model '{model}' (duration: {duration}s, upscaled)..."
        )
        res = self._post("generate", payload)
        jobs = res.get("jobs", [])
        if not jobs:
            raise RuntimeError(f"No jobs returned from flow_generate: {res}")

        job_id = jobs[0]["id"]
        logger.info(f"Queued video job: {job_id}. Waiting for completion...")
        wait_res = self.wait_for_jobs(job_ids=[job_id], timeout_seconds=600)

        output_file = None
        for j in wait_res.get("jobs", []):
            if j.get("id") == job_id:
                files = j.get("files", [])
                if files:
                    output_file = Path(files[0])
                break

        if not output_file or not output_file.exists():
            proj_dir = FLOW_OUTPUT_ROOT / project_name
            candidates = list(proj_dir.glob("*.mp4"))
            if candidates:
                output_file = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]

        if not output_file or not output_file.exists():
            raise FileNotFoundError(f"Could not find generated video file for job {job_id}")

        shutil.copy2(output_file, dest_video_path)
        logger.info(f"Generated video clip saved to {dest_video_path}")
        return dest_video_path


def load_job_state(state_file: Path) -> dict:
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"scenes": {}}


def save_job_state(state_file: Path, state_data: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state_data, f, indent=2, ensure_ascii=False)


def run_stage_4(
    prompts_list: list[dict],
    avatar_path: Path,
    job_dir: Path,
    job_id: str,
    cooldown_sec: float = 15.0,
    model_image: str = DEFAULT_IMAGE_MODEL,
    model_video: str = DEFAULT_VIDEO_MODEL,
) -> dict[str, dict]:
    """
    Executes Stage 4: Sequential Image + Video generation via Flow MCP server.
    Saves state after each clip to enable safe resumption if interrupted.
    Returns dictionary mapping scene_id -> {image_path, clip_path, dialogue}.
    """
    client = FlowClient()
    client.check_status()

    images_dir = job_dir / "generated_images"
    clips_dir = job_dir / "clips"
    state_file = job_dir / "state.json"

    images_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    state = load_job_state(state_file)
    completed_scenes = state.setdefault("scenes", {})

    total_scenes = len(prompts_list)

    for i, scene in enumerate(prompts_list, start=1):
        scene_id = scene["scene_id"]
        scene_num = scene.get("scene_num", i)
        dialogue = scene.get("dialogue", "")
        img_prompt = scene["image_prompt"]
        vid_prompt = scene["video_prompt"]
        duration = scene.get("omni_duration", 4)

        img_dest = images_dir / f"img_{scene_num:02d}.png"
        clip_dest = clips_dir / f"clip_{scene_num:02d}.mp4"

        # Check if already completed
        prev_entry = completed_scenes.get(scene_id, {})
        if (
            prev_entry.get("status") == "completed"
            and clip_dest.exists()
            and img_dest.exists()
        ):
            logger.info(f"Skipping {scene_id} ({i}/{total_scenes}) - already completed.")
            continue

        logger.info(f"\n==========================================")
        logger.info(f"Processing {scene_id} ({i}/{total_scenes})")
        logger.info(f"Dialogue: \"{dialogue}\"")
        logger.info(f"==========================================")

        # 1. Image Pass (Banana Pro 2.1)
        if not img_dest.exists():
            logger.info(f"[{scene_id}] Generating avatar keyframe image (Banana Pro 2.1)...")
            sc_path = scene.get("screenshot_path")
            client.generate_image_scene(
                project_name=f"Recreation_{job_id}",
                prompt=img_prompt,
                avatar_path=avatar_path,
                screenshot_path=Path(sc_path) if sc_path else None,
                dest_image_path=img_dest,
                model=model_image,
            )
            completed_scenes[scene_id] = {
                "status": "image_done",
                "image_path": str(img_dest.resolve()),
                "dialogue": dialogue,
            }
            save_job_state(state_file, state)
            time.sleep(3.0)  # brief stabilization pause
        else:
            logger.info(f"[{scene_id}] Reusing existing image: {img_dest}")

        # 2. Video Pass (OmniFlash 1.1)
        if not clip_dest.exists():
            logger.info(f"[{scene_id}] Generating video clip (OmniFlash 1.1, duration={duration}s)...")
            client.generate_video_scene(
                project_name=f"Recreation_{job_id}",
                prompt=vid_prompt,
                first_frame_path=img_dest,
                dest_video_path=clip_dest,
                duration=duration,
                model=model_video,
            )
            completed_scenes[scene_id] = {
                "status": "completed",
                "image_path": str(img_dest.resolve()),
                "video_path": str(clip_dest.resolve()),
                "dialogue": dialogue,
                "duration": duration,
            }
            save_job_state(state_file, state)
        else:
            logger.info(f"[{scene_id}] Reusing existing clip: {clip_dest}")

        # Cooldown between scene jobs to avoid rate limits
        if i < total_scenes and cooldown_sec > 0:
            logger.info(f"Cooldown pause: waiting {cooldown_sec:.1f}s before next scene...")
            time.sleep(cooldown_sec)

    logger.info(f"\nAll {total_scenes} scenes processed successfully.")
    return completed_scenes
