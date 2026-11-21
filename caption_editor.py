#!/usr/bin/env python3
"""
FlowReel Caption & Editing Tool
--------------------------------
Stitches Google Flow clips into a normalized 9:16 vertical reel,
transcribes speech with word-level precision, and burns viral short-form
captions where the text appears white with a black outline and each spoken
word pops in vibrant yellow (#FFE135) with scale bounce as it is spoken.

Usage:
    # 1. Automatic stitch & burn from a completed Flow job:
    python caption_editor.py --job-id job_20261121_000652_0bcc7f

    # 2. Burn viral pop captions on an existing video:
    python caption_editor.py --video path/to/video.mp4 --style yellow-pop

    # 3. Custom positioning and styling:
    python caption_editor.py --job-id <JOB_ID> --style yellow-pop --words-per-screen 3 --position lower-third
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("caption_editor")

# Import internal pipeline modules
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from src.captions import (
    COLOR_PALETTES,
    POSITIONS,
    extract_words_from_whisper,
    generate_pop_subtitles_ass,
    burn_pop_captions_into_video,
)
from src.editor import concatenate_clips, format_srt_time
from src.ingest import get_whisper_model


def get_job_directory(job_id_or_path: str) -> Path:
    """Resolves job directory from job ID or direct path."""
    p = Path(job_id_or_path)
    if p.exists() and p.is_dir():
        return p.resolve()
    
    jobs_dir = BASE_DIR / "jobs"
    candidate = jobs_dir / job_id_or_path
    if candidate.exists() and candidate.is_dir():
        return candidate.resolve()
    
    # Try finding by prefix
    matches = list(jobs_dir.glob(f"*{job_id_or_path}*"))
    if matches and matches[0].is_dir():
        return matches[0].resolve()

    raise FileNotFoundError(f"Job directory not found for: {job_id_or_path}")


def process_video_captions(
    video_path: Path,
    output_path: Path,
    style: str = "yellow-pop",
    position: str = "lower-third",
    font_name: str = "Arial Black",
    font_size: int = 28,
    words_per_screen: int = 3,
    uppercase: bool = True,
    lead_seconds: float = 0.08,
    model_size: str = "base",
    save_srt: bool = True,
) -> dict[str, Path]:
    """
    Transcribes video using faster-whisper with word timestamps,
    generates ASS subtitles with yellow-pop effect, and burns into output video.
    """
    logger.info(f"Loading faster-whisper (model={model_size}) to transcribe {video_path.name}...")
    model = get_whisper_model(model_size)

    segments_iter, info = model.transcribe(
        str(video_path),
        word_timestamps=True,
        vad_filter=True,
    )
    segments = list(segments_iter)
    logger.info(f"Detected language: {info.language} (duration: {info.duration:.2f}s, segments: {len(segments)})")

    # 1. Extract words
    words = extract_words_from_whisper(segments)
    logger.info(f"Extracted {len(words)} word tokens with precise timestamps.")

    if not words:
        logger.warning("No words detected in audio; copying video without subtitles.")
        import shutil
        shutil.copy2(video_path, output_path)
        return {"video": output_path}

    # 2. Generate standard SRT if requested
    out_dir = output_path.parent
    base_name = output_path.stem
    srt_path = out_dir / f"{base_name}.srt"
    if save_srt:
        srt_entries = []
        for idx, seg in enumerate(segments, start=1):
            txt = seg.text.strip()
            if not txt:
                continue
            srt_entries.append(f"{idx}\n{format_srt_time(seg.start)} --> {format_srt_time(seg.end)}\n{txt}\n")
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_entries) + "\n")
        logger.info(f"Saved standard SRT -> {srt_path}")

    # 3. Save words JSON for inspector / web UI
    words_json_path = out_dir / f"{base_name}_words.json"
    with open(words_json_path, "w", encoding="utf-8") as f:
        json.dump(words, f, indent=2)

    # 4. Generate dynamic ASS subtitles
    ass_path = out_dir / f"{base_name}.ass"
    generate_pop_subtitles_ass(
        words=words,
        output_path=ass_path,
        style=style,
        position=position,
        font_name=font_name,
        font_size=font_size,
        max_words_per_screen=words_per_screen,
        uppercase=uppercase,
        lead_seconds=lead_seconds,
    )

    # 5. Burn captions via FFmpeg libass
    burn_pop_captions_into_video(
        video_path=video_path,
        ass_path=ass_path,
        output_path=output_path,
    )

    return {
        "video": output_path,
        "ass": ass_path,
        "srt": srt_path,
        "words": words_json_path,
    }


def process_job(
    job_dir: Path,
    style: str = "yellow-pop",
    position: str = "lower-third",
    font_name: str = "Arial Black",
    font_size: int = 28,
    words_per_screen: int = 3,
    uppercase: bool = True,
    model_size: str = "base",
) -> dict[str, Path]:
    """
    Orchestrates full post-generation editing for a job:
    1. Collects and stitches clips from jobs/<job_id>/clips/
    2. Transcribes with word timestamps
    3. Generates yellow-pop captions and burns them into final_reel_pop_captioned.mp4
    """
    clips_dir = job_dir / "clips"
    final_dir = job_dir / "final"
    work_dir = job_dir / "temp_edit"

    clip_files = sorted(
        clips_dir.glob("clip_*.mp4"),
        key=lambda p: int(re.search(r"\d+", p.stem).group(0)) if re.search(r"\d+", p.stem) else 0,
    )

    if not clip_files:
        raise FileNotFoundError(f"No clips found in {clips_dir} to stitch.")

    logger.info(f"Found {len(clip_files)} clips in {clips_dir}: {[c.name for c in clip_files]}")

    final_reel = final_dir / "final_reel.mp4"
    if not final_reel.exists() or final_reel.stat().st_size < 1000:
        logger.info(f"Concatenating and normalizing {len(clip_files)} clips to {final_reel}...")
        concatenate_clips(clip_files, final_reel, work_dir)
    else:
        logger.info(f"Using existing stitched video: {final_reel}")

    final_captioned = final_dir / "final_reel_pop_captioned.mp4"
    results = process_video_captions(
        video_path=final_reel,
        output_path=final_captioned,
        style=style,
        position=position,
        font_name=font_name,
        font_size=font_size,
        words_per_screen=words_per_screen,
        uppercase=uppercase,
        model_size=model_size,
    )
    results["final_reel"] = final_reel
    return results


def main():
    parser = argparse.ArgumentParser(
        description="FlowReel Caption & Editing Tool — Stitches Flow clips & burns viral yellow-pop captions."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--job-id",
        type=str,
        help="Job ID or folder name in jobs/ to stitch and caption.",
    )
    group.add_argument(
        "--video",
        type=str,
        help="Path to an existing standalone video to transcribe and caption.",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output video path (defaults to final_reel_pop_captioned.mp4 or <video>_captioned.mp4).",
    )
    parser.add_argument(
        "--style",
        type=str,
        choices=list(COLOR_PALETTES.keys()),
        default="yellow-pop",
        help="Caption style palette (default: yellow-pop).",
    )
    parser.add_argument(
        "--position",
        type=str,
        choices=list(POSITIONS.keys()),
        default="lower-third",
        help="Vertical position on screen (default: lower-third).",
    )
    parser.add_argument(
        "--words-per-screen",
        type=int,
        default=3,
        help="Number of words to show simultaneously on screen (default: 3).",
    )
    parser.add_argument(
        "--font",
        type=str,
        default="Arial Black",
        help="Font family name (default: Arial Black).",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=28,
        help="Base font size (default: 28).",
    )
    parser.add_argument(
        "--case",
        type=str,
        choices=["upper", "original"],
        default="upper",
        help="Text case (default: upper for short-form punchy reels).",
    )
    parser.add_argument(
        "--model-size",
        type=str,
        default="base",
        help="Whisper model size (base, small, medium).",
    )

    args = parser.parse_args()

    uppercase = (args.case == "upper")

    try:
        if args.job_id:
            job_dir = get_job_directory(args.job_id)
            logger.info(f"Processing Flow job: {job_dir.name}")
            results = process_job(
                job_dir=job_dir,
                style=args.style,
                position=args.position,
                font_name=args.font,
                font_size=args.font_size,
                words_per_screen=args.words_per_screen,
                uppercase=uppercase,
                model_size=args.model_size,
            )
            print("\n==================================================")
            print("🎬 CAPTION & EDITING COMPLETE!")
            print(f"Stitched Video   : {results.get('final_reel')}")
            print(f"Captioned Reel   : {results.get('video')}")
            print(f"Subtitles (.ass) : {results.get('ass')}")
            print(f"Captions (.srt)  : {results.get('srt')}")
            print("==================================================")

        elif args.video:
            video_path = Path(args.video).resolve()
            if not video_path.exists():
                logger.error(f"Input video not found: {video_path}")
                sys.exit(1)

            if args.output:
                out_path = Path(args.output).resolve()
            else:
                out_path = video_path.parent / f"{video_path.stem}_pop_captioned.mp4"

            logger.info(f"Processing standalone video: {video_path}")
            results = process_video_captions(
                video_path=video_path,
                output_path=out_path,
                style=args.style,
                position=args.position,
                font_name=args.font,
                font_size=args.font_size,
                words_per_screen=args.words_per_screen,
                uppercase=uppercase,
                model_size=args.model_size,
            )
            print("\n==================================================")
            print("🎬 CAPTIONING COMPLETE!")
            print(f"Captioned Video  : {results.get('video')}")
            print(f"Subtitles (.ass) : {results.get('ass')}")
            print("==================================================")

    except Exception as e:
        logger.error(f"Caption editor execution failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
