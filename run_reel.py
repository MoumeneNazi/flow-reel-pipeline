#!/usr/bin/env python3
"""
Flow Reel Recreation Pipeline
Single-command CLI entrypoint for automated video recreation via Google Flow.

Usage:
    python run_reel.py "<VIDEO_URL_OR_PATH>" --avatar "./avatar.png"
    python run_reel.py "<VIDEO_URL_OR_PATH>" --dry-run
    python run_reel.py "<VIDEO_URL_OR_PATH>" --avatar "./avatar.png" --burn-captions
"""

import argparse
import datetime
import hashlib
import json
import logging
from pathlib import Path
import sys

from src.config import (
    DEFAULT_SCENE_THRESHOLD,
    DEFAULT_COOLDOWN_SEC,
    DEFAULT_IMAGE_MODEL,
    DEFAULT_VIDEO_MODEL,
)
from src.ingest import run_stage_1
from src.scenes import run_stage_2
from src.prompting import run_stage_3
from src.flow_client import run_stage_4
from src.review import run_stage_5_interactive, display_review_table, regenerate_clip
from src.editor import run_stage_6

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("flow_reel")


def generate_job_id(source: str) -> str:
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    source_hash = hashlib.md5(source.encode("utf-8")).hexdigest()[:6]
    return f"job_{timestamp}_{source_hash}"


def main():
    parser = argparse.ArgumentParser(
        description="Automated Video Recreation Pipeline using Google Flow (Banana Pro 2.1 & OmniFlash 1.1)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "video_source",
        type=str,
        help="Source video URL (YouTube, Instagram, TikTok, etc.) or local .mp4 file path",
    )
    parser.add_argument(
        "--avatar",
        type=str,
        default=None,
        help="Path to AI avatar reference image (PNG/JPG). Required unless running with --dry-run.",
    )
    parser.add_argument(
        "--job-id",
        type=str,
        default=None,
        help="Optional custom job identifier. Defaults to timestamp-based ID.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_SCENE_THRESHOLD,
        help="PySceneDetect ContentDetector threshold (lower = more cuts, higher = fewer cuts).",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=DEFAULT_COOLDOWN_SEC,
        help="Cooldown pause (seconds) between Flow generation scenes to avoid rate limits.",
    )
    parser.add_argument(
        "--model-image",
        type=str,
        default=DEFAULT_IMAGE_MODEL,
        help="Google Flow image model name for keyframe generation (Pass 1).",
    )
    parser.add_argument(
        "--model-video",
        type=str,
        default=DEFAULT_VIDEO_MODEL,
        help="Google Flow video model name for video generation (Pass 2).",
    )
    parser.add_argument(
        "--burn-captions",
        action="store_true",
        help="Burn in formatted viral pop captions into final_reel_captioned.mp4 in Stage 6.",
    )
    parser.add_argument(
        "--caption-style",
        type=str,
        default="yellow-pop",
        choices=["yellow-pop", "green-pop", "cyan-pop", "clean-white"],
        help="Viral caption style palette (default: yellow-pop).",
    )
    parser.add_argument(
        "--caption-position",
        type=str,
        default="lower-third",
        choices=["lower-third", "middle", "bottom"],
        help="Vertical screen placement for captions (default: lower-third).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run Stages 1–3 only (download, transcribe, scene detection, and prompt mapping). Stops before spending Google Flow credits.",
    )
    parser.add_argument(
        "--jobs-dir",
        type=str,
        default="./jobs",
        help="Root directory for storing job outputs and artifacts.",
    )
    parser.add_argument(
        "--stop-at-review",
        action="store_true",
        help="Generate all clips through Stage 4, print the inspection review table, and pause cleanly without blocking on input.",
    )
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Approve generated clips directly and proceed immediately to Stage 6 (Final Edit & SRT Captions).",
    )
    parser.add_argument(
        "--regen-clip",
        type=int,
        default=None,
        help="Regenerate a specific clip number directly (e.g. --regen-clip 3).",
    )
    parser.add_argument(
        "--regen-prompt",
        type=str,
        default=None,
        help="Optional prompt adjustment when using --regen-clip.",
    )
    parser.add_argument(
        "--regen-image",
        action="store_true",
        help="Also regenerate keyframe image when using --regen-clip.",
    )

    args = parser.parse_args()

    # Validate avatar argument
    avatar_path = None
    if args.avatar:
        avatar_path = Path(args.avatar).resolve()
        if not avatar_path.is_file():
            logger.error(f"Avatar file not found at: {avatar_path}")
            sys.exit(1)
    elif not args.dry_run:
        logger.error("--avatar is required for recreation (or use --dry-run to test Stages 1–3 without spending Flow credits).")
        sys.exit(1)

    # Initialize job folder
    job_id = args.job_id or generate_job_id(args.video_source)
    job_dir = Path(args.jobs_dir).resolve() / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 80)
    print("🚀 STARTING AUTOMATED VIDEO RECREATION PIPELINE")
    print("=" * 80)
    print(f"Job ID        : {job_id}")
    print(f"Source        : {args.video_source}")
    print(f"Avatar        : {avatar_path or '[None - Dry Run Mode]'}")
    print(f"Job Directory : {job_dir}")
    print(f"Dry Run Mode  : {'ENABLED (Stages 1-3 only)' if args.dry_run else 'DISABLED (Full Run)'}")
    print("=" * 80 + "\n")

    # =========================================================================
    # STAGE 1: Ingest & Transcribe
    # =========================================================================
    print("▶ STAGE 1: Ingesting Video & Transcribing with faster-whisper...")
    source_video_path, transcript_path = run_stage_1(
        video_source=args.video_source,
        job_dir=job_dir,
    )
    print(f"✔ Stage 1 Complete: Video -> {source_video_path.name}, Transcript -> {transcript_path.name}\n")

    # =========================================================================
    # STAGE 2: Transition Screenshots
    # =========================================================================
    print(f"▶ STAGE 2: Detecting Scene Transitions (PySceneDetect threshold={args.threshold})...")
    scenes_dir, scenes_csv_path, scenes_data = run_stage_2(
        video_path=source_video_path,
        job_dir=job_dir,
        threshold=args.threshold,
    )
    print(f"✔ Stage 2 Complete: Detected {len(scenes_data)} scenes. Screenshots saved to {scenes_dir.name}/\n")

    # =========================================================================
    # STAGE 3: Scene-to-Prompt Mapping
    # =========================================================================
    print("▶ STAGE 3: Mapping Spoken Dialogue to Flow Prompts...")
    prompts_path, prompts_list = run_stage_3(
        scenes_data=scenes_data,
        transcript_path=transcript_path,
        job_dir=job_dir,
        avatar_path=avatar_path,
    )
    print(f"✔ Stage 3 Complete: Generated {len(prompts_list)} prompts in {prompts_path.name}\n")

    # Handle individual clip regeneration if requested
    if args.regen_clip is not None:
        print(f"\n▶ Regenerating clip {args.regen_clip}...")
        regenerate_clip(
            scene_num=args.regen_clip,
            prompt_adjustment=args.regen_prompt,
            job_dir=job_dir,
            job_id=job_id,
            prompts_list=prompts_list,
            avatar_path=avatar_path,
            regen_image=args.regen_image,
        )
        display_review_table(job_dir / "clips", prompts_list)
        return

    # Check for dry-run exit
    if args.dry_run:
        print("=" * 80)
        print("🏁 DRY RUN COMPLETE — Zero Google Flow credits spent.")
        print("=" * 80)
        print(f"Review the generated scenes and prompts here:")
        print(f"  • Scenes CSV   : {scenes_csv_path}")
        print(f"  • Screenshots  : {scenes_dir}")
        print(f"  • Prompts JSON : {prompts_path}")
        print("\nPrompt Summary:")
        for p in prompts_list:
            print(f"  [{p['scene_id']}] ({p['original_duration']}s -> {p['omni_duration']}s Omni)")
            print(f"    Dialogue : \"{p['dialogue'] or '[no dialogue]'}\"")
            print(f"    Image    : {p['image_prompt'][:90]}...")
            print(f"    Video    : {p['video_prompt'][:90]}...")
        print("\nTo generate the recreation with Google Flow, re-run with your --avatar image (without --dry-run):")
        print(f"  python run_reel.py \"{args.video_source}\" --avatar \"path/to/avatar.png\" --job-id \"{job_id}\"")
        print("=" * 80)
        return

    # =========================================================================
    # STAGE 4: Google Flow Generation
    # =========================================================================
    print("▶ STAGE 4: Generating Scenes via Google Flow (GTA1 MCP Flow)...")
    print(f"  • Image Pass : {args.model_image} (0 credits)")
    print(f"  • Video Pass : {args.model_video} (OmniFlash 1.1, ~7-12 credits/scene)")
    print(f"  • Cooldown   : {args.cooldown}s between scenes")

    run_stage_4(
        prompts_list=prompts_list,
        avatar_path=avatar_path,
        job_dir=job_dir,
        job_id=job_id,
        cooldown_sec=args.cooldown,
        model_image=args.model_image,
        model_video=args.model_video,
    )
    print("✔ Stage 4 Complete: All scene clips generated.\n")

    # =========================================================================
    # STAGE 5: MANDATORY Human-in-the-Loop Review Checkpoint
    # =========================================================================
    if args.stop_at_review:
        print("\n⏸ Halting at Review Checkpoint (--stop-at-review specified):")
        display_review_table(job_dir / "clips", prompts_list)
        print(f"\nAll generated clips are ready for inspection in: {job_dir / 'clips'}")
        print("To approve and run Stage 6 edit, run:")
        print(f"  python run_reel.py \"{args.video_source}\" --avatar \"{avatar_path}\" --job-id \"{job_id}\" --approve")
        return

    if args.approve:
        print("\n✔ Direct approval received (--approve specified). Skipping interactive review prompt.")
        approved = True
    else:
        approved = run_stage_5_interactive(
            job_dir=job_dir,
            job_id=job_id,
            prompts_list=prompts_list,
            avatar_path=avatar_path,
        )

    if not approved:
        print(f"\nPipeline halted. Job data preserved in: {job_dir}")
        return

    # =========================================================================
    # STAGE 6: Final Edit & SRT Captions (Only After Approval)
    # =========================================================================
    print("\n▶ STAGE 6: Concatenating Clips & Generating Accurate SRT Captions...")
    edit_results = run_stage_6(
        job_dir=job_dir,
        burn_captions=args.burn_captions,
        caption_style=args.caption_style,
        caption_position=args.caption_position,
    )

    print("\n" + "=" * 80)
    print("🎉 VIDEO RECREATION PIPELINE COMPLETED SUCCESSFULLY!")
    print("=" * 80)
    print(f"Final Reel Master Video : {edit_results['final_reel']}")
    print(f"Frame-Accurate SRT      : {edit_results['final_srt']}")
    if "final_captioned" in edit_results:
        print(f"Captioned Reel Video    : {edit_results['final_captioned']}")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
