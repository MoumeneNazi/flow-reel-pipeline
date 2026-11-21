"""
Antigravity Dynamic Workflow for Flow Reel Recreation Pipeline.
Can be executed with run_workflow tool or imported directly.
"""

import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.ingest import run_stage_1
from src.scenes import run_stage_2
from src.prompting import run_stage_3
from src.flow_client import run_stage_4
from src.editor import run_stage_6
from src.review import display_review_table


async def run_flow_reel_workflow(
    video_source: str,
    avatar: str | None = None,
    dry_run: bool = False,
    burn_captions: bool = False,
    job_id: str = "workflow_job",
    threshold: float = 27.0,
    cooldown: float = 15.0,
):
    job_dir = PROJECT_ROOT / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    avatar_path = Path(avatar).resolve() if avatar else None

    # Stage 1
    log(f"Phase 1: Ingesting {video_source} and transcribing...")
    source_video_path, transcript_path = run_stage_1(video_source, job_dir)
    log(f"Stage 1 complete: {source_video_path.name}, transcript: {transcript_path.name}")

    # Stage 2
    log(f"Phase 2: Detecting scenes (threshold={threshold})...")
    scenes_dir, scenes_csv_path, scenes_data = run_stage_2(source_video_path, job_dir, threshold=threshold)
    log(f"Stage 2 complete: {len(scenes_data)} scenes detected.")

    # Stage 3
    log(f"Phase 3: Mapping prompts...")
    prompts_path, prompts_list = run_stage_3(scenes_data, transcript_path, job_dir, avatar_path=avatar_path)
    log(f"Stage 3 complete: prompts written to {prompts_path.name}.")

    if dry_run:
        log("Dry run requested. Stopping before Google Flow generation.")
        return {
            "status": "dry_run_complete",
            "scenes_csv": str(scenes_csv_path),
            "prompts_json": str(prompts_path),
            "scenes_dir": str(scenes_dir),
        }

    if not avatar_path or not avatar_path.exists():
        raise ValueError("Avatar reference image is required for Flow generation.")

    # Stage 4
    log("Phase 4: Running Google Flow generation...")
    scenes_output = run_stage_4(
        prompts_list=prompts_list,
        avatar_path=avatar_path,
        job_dir=job_dir,
        job_id=job_id,
        cooldown_sec=cooldown,
    )
    log(f"Stage 4 complete: {len(scenes_output)} scenes generated.")

    # Stage 5
    clips_dir = job_dir / "clips"
    display_review_table(clips_dir, prompts_list)
    log(f"Stage 5 Checkpoint: clips are ready for review in {clips_dir}")

    # Stage 6
    log("Phase 6: Concatenating clips and generating accurate SRT...")
    edit_results = run_stage_6(job_dir, burn_captions=burn_captions)
    log(f"Stage 6 complete! Final reel: {edit_results['final_reel']}")

    return {
        "status": "completed",
        "final_reel": str(edit_results["final_reel"]),
        "final_srt": str(edit_results["final_srt"]),
        "captioned_reel": str(edit_results.get("final_captioned", "")),
    }


# Entrypoint when invoked via run_workflow tool
if "args" in globals():
    video = args.get("video_source")
    avatar = args.get("avatar")
    dry_run = args.get("dry_run", False)
    burn_captions = args.get("burn_captions", False)
    job_id = args.get("job_id", "workflow_job")
    threshold = float(args.get("threshold", 27.0))
    cooldown = float(args.get("cooldown", 15.0))

    if not video:
        raise ValueError("video_source is required in args")

    # Execute
    import asyncio
    asyncio.run(run_flow_reel_workflow(
        video_source=video,
        avatar=avatar,
        dry_run=dry_run,
        burn_captions=burn_captions,
        job_id=job_id,
        threshold=threshold,
        cooldown=cooldown,
    ))
