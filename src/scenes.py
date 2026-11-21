"""
Stage 2 — Transition Screenshots:
Uses PySceneDetect (detect-content) to detect scenes and extracts middle frames.
"""

from pathlib import Path
import csv
import logging
import cv2

logger = logging.getLogger("flow_reel.scenes")


def detect_and_extract_scenes(
    video_path: Path,
    output_scenes_dir: Path,
    scenes_csv_path: Path,
    threshold: float = 27.0,
    min_scene_len_sec: float = 1.0,
) -> list[dict]:
    """
    Detects scene transitions in `video_path` using PySceneDetect ContentDetector.
    Extracts middle frame of each scene into `output_scenes_dir/scene_XX.jpg`.
    Writes `scenes_csv_path` containing exact timestamps and frame indices.
    Returns list of scene metadata dictionaries.
    """
    from scenedetect import open_video, SceneManager, ContentDetector

    output_scenes_dir.mkdir(parents=True, exist_ok=True)
    scenes_csv_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info(f"Opening video for scene detection: {video_path}")
    video = open_video(str(video_path))
    scene_manager = SceneManager()

    # PySceneDetect ContentDetector with user configurable threshold
    scene_manager.add_detector(ContentDetector(threshold=threshold))

    logger.info(f"Detecting scenes with ContentDetector(threshold={threshold})...")
    scene_manager.detect_scenes(video)
    scene_list = scene_manager.get_scene_list()

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    total_duration = total_frames / fps if fps > 0 else 0.0

    # Fallback if no cuts are detected: treat entire video as single scene
    if not scene_list:
        logger.warning("No scene cuts detected; treating entire video as a single scene.")
        from scenedetect import FrameTimecode
        start_tc = FrameTimecode(0, fps=fps)
        end_tc = FrameTimecode(total_frames, fps=fps)
        scene_list = [(start_tc, end_tc)]

    scenes_data = []

    for idx, (start_tc, end_tc) in enumerate(scene_list, start=1):
        scene_id = f"scene_{idx:02d}"
        start_sec = round(start_tc.get_seconds(), 3)
        end_sec = round(end_tc.get_seconds(), 3)
        duration = round(end_sec - start_sec, 3)

        start_frame = start_tc.get_frames()
        end_frame = end_tc.get_frames()
        middle_frame = (start_frame + end_frame) // 2
        middle_sec = round(middle_frame / fps, 3)

        screenshot_filename = f"{scene_id}.jpg"
        screenshot_path = output_scenes_dir / screenshot_filename

        # Extract middle frame using OpenCV
        cap.set(cv2.CAP_PROP_POS_FRAMES, middle_frame)
        ret, frame = cap.read()
        if ret and frame is not None:
            cv2.imwrite(str(screenshot_path), frame)
            logger.info(f"Extracted {scene_id} middle frame (frame {middle_frame}, {middle_sec}s) -> {screenshot_path.name}")
        else:
            logger.error(f"Could not read frame {middle_frame} for {scene_id}")

        scene_item = {
            "scene_id": scene_id,
            "scene_num": idx,
            "start_time": start_sec,
            "end_time": end_sec,
            "duration": duration,
            "start_frame": start_frame,
            "end_frame": end_frame,
            "middle_frame": middle_frame,
            "middle_time": middle_sec,
            "screenshot_path": str(screenshot_path.resolve()),
        }
        scenes_data.append(scene_item)

    cap.release()

    # Save to scenes.csv
    fieldnames = [
        "scene_id",
        "scene_num",
        "start_time",
        "end_time",
        "duration",
        "start_frame",
        "end_frame",
        "middle_frame",
        "middle_time",
        "screenshot_path",
    ]
    with open(scenes_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(scenes_data)

    logger.info(f"Detected {len(scenes_data)} scenes. Saved metadata to {scenes_csv_path}")
    return scenes_data


def run_stage_2(
    video_path: Path,
    job_dir: Path,
    threshold: float = 27.0,
) -> tuple[Path, Path, list[dict]]:
    """
    Executes Stage 2: Scene transition detection & screenshots.
    Returns (scenes_dir, scenes_csv_path, scenes_data).
    """
    scenes_dir = job_dir / "scenes"
    scenes_csv = job_dir / "scenes.csv"

    if scenes_csv.exists() and scenes_dir.exists() and any(scenes_dir.glob("*.jpg")):
        logger.info(f"Reusing existing detected scenes from {scenes_csv}")
        import csv
        scenes_data = []
        with open(scenes_csv, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                scenes_data.append({
                    "scene_id": row["scene_id"],
                    "scene_num": int(row["scene_num"]),
                    "start_time": float(row["start_time"]),
                    "end_time": float(row["end_time"]),
                    "duration": float(row["duration"]),
                    "screenshot_path": row["screenshot_path"],
                })
        return scenes_dir, scenes_csv, scenes_data

    scenes_data = detect_and_extract_scenes(
        video_path=video_path,
        output_scenes_dir=scenes_dir,
        scenes_csv_path=scenes_csv,
        threshold=threshold,
    )

    return scenes_dir, scenes_csv, scenes_data
