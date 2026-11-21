"""
Stage 1 — Ingest & Transcribe:
Downloads video from URL (or copies local source) and transcribes with faster-whisper.
"""

from pathlib import Path
import json
import logging
import shutil
import subprocess
import sys

logger = logging.getLogger("flow_reel.ingest")


def download_source_video(video_source: str, output_path: Path) -> Path:
    """
    Downloads source video using yt-dlp at maximum quality, or copies from a local file path.
    Output is saved to `output_path` (e.g., ./jobs/<job_id>/source.mp4).
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Check if video_source is a local file
    local_source = Path(video_source)
    if local_source.is_file():
        logger.info(f"Using local source file: {local_source}")
        if local_source.resolve() != output_path.resolve():
            shutil.copy2(local_source, output_path)
        return output_path

    # Otherwise treat as URL and download via yt-dlp
    logger.info(f"Downloading source video from URL: {video_source}")
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--format",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        str(output_path),
        "--no-playlist",
        video_source,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"yt-dlp failed: {result.stderr}")
        raise RuntimeError(f"Failed to download video from {video_source}: {result.stderr}")

    if not output_path.exists():
        # Sometimes yt-dlp appends or changes extension; find the resulting file
        matches = list(output_path.parent.glob("source.*"))
        if matches:
            actual = matches[0]
            if actual != output_path:
                shutil.move(str(actual), str(output_path))
        else:
            raise FileNotFoundError(f"Expected downloaded video at {output_path}, but not found.")

    logger.info(f"Source video saved successfully to {output_path}")
    return output_path


def get_whisper_model(model_size: str = "base"):
    from faster_whisper import WhisperModel

    # Check local pre-downloaded cache directory first
    local_dir = Path.home() / ".cache" / "whisper_models" / model_size
    if (local_dir / "model.bin").exists() and (local_dir / "config.json").exists():
        logger.info(f"Loading local Whisper model from {local_dir}...")
        return WhisperModel(str(local_dir), device="cpu", compute_type="int8")

    tiny_snap = (
        Path.home()
        / ".cache"
        / "huggingface"
        / "hub"
        / "models--Systran--faster-whisper-tiny"
        / "snapshots"
        / "d90ca5fe260221311c53c58e660288d3deb8d356"
    )
    if (tiny_snap / "model.bin").exists():
        logger.info(f"Loading local cached Whisper model from {tiny_snap}...")
        return WhisperModel(str(tiny_snap), device="cpu", compute_type="int8")

    return WhisperModel(model_size, device="cpu", compute_type="int8")


def transcribe_video(
    video_path: Path,
    output_transcript_path: Path,
    model_size: str = "base",
    language: str | None = None,
) -> dict:
    """
    Transcribes audio from video_path using faster-whisper with word-level timestamps.
    Saves and returns transcript.json.
    """
    logger.info(f"Transcribing {video_path} with faster-whisper (model={model_size})...")
    output_transcript_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        model = get_whisper_model(model_size)
    except ImportError:
        raise ImportError(
            "faster-whisper is not installed. Please run: pip install faster-whisper"
        )
    segments_iter, info = model.transcribe(
        str(video_path),
        word_timestamps=True,
        language=language,
        vad_filter=True,
    )

    detected_language = info.language
    logger.info(
        f"Detected language: {detected_language} (probability: {info.language_probability:.2f})"
    )

    segments_data = []
    full_text_parts = []

    for seg in segments_iter:
        seg_dict = {
            "id": seg.id,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": seg.text.strip(),
            "words": [
                {
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "probability": round(w.probability, 3),
                }
                for w in (seg.words or [])
            ],
        }
        segments_data.append(seg_dict)
        if seg.text.strip():
            full_text_parts.append(seg.text.strip())

    transcript_result = {
        "language": detected_language,
        "language_probability": round(info.language_probability, 3),
        "duration": round(info.duration, 3),
        "full_text": " ".join(full_text_parts),
        "segments": segments_data,
    }

    with open(output_transcript_path, "w", encoding="utf-8") as f:
        json.dump(transcript_result, f, indent=2, ensure_ascii=False)

    logger.info(
        f"Transcription complete: {len(segments_data)} segments saved to {output_transcript_path}"
    )
    return transcript_result


def run_stage_1(
    video_source: str,
    job_dir: Path,
    model_size: str = "base",
    language: str | None = None,
) -> tuple[Path, Path]:
    """
    Executes Stage 1: Download & Transcribe.
    Returns (source_video_path, transcript_path).
    """
    source_video = job_dir / "source.mp4"
    transcript_file = job_dir / "transcript.json"

    if source_video.exists() and source_video.stat().st_size > 0:
        logger.info(f"Reusing existing source video at: {source_video}")
    else:
        download_source_video(video_source, source_video)

    if transcript_file.exists() and transcript_file.stat().st_size > 0:
        logger.info(f"Reusing existing transcript at: {transcript_file}")
    else:
        transcribe_video(
            source_video,
            transcript_file,
            model_size=model_size,
            language=language,
        )

    return source_video, transcript_file
