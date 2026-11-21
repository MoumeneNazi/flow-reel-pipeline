"""
Stage 5 — MANDATORY Human-in-the-Loop Review Checkpoint:
Halts before editing, displays clips with dialogue, and supports interactive regeneration or approval.
"""

from pathlib import Path
import json
import logging
import re
import sys

from .flow_client import FlowClient

logger = logging.getLogger("flow_reel.review")


def display_review_table(clips_dir: Path, prompts_list: list[dict]) -> None:
    """Displays a formatted table of all generated clips and their dialogue."""
    abs_clips_dir = clips_dir.resolve()
    print("\n" + "=" * 80)
    print("🎬 MANDATORY HUMAN-IN-THE-LOOP REVIEW CHECKPOINT")
    print("=" * 80)
    print(f"Inspection Directory: {abs_clips_dir}")
    print(f"File URI: file:///{str(abs_clips_dir).replace('\\', '/')}")
    print("-" * 80)
    print(f"{'Clip':<12} | {'Duration':<10} | {'Status':<10} | {'Dialogue'}")
    print("-" * 80)

    for p in prompts_list:
        scene_num = p.get("scene_num", 1)
        clip_name = f"clip_{scene_num:02d}.mp4"
        clip_path = clips_dir / clip_name
        dur_str = f"{p.get('omni_duration', 4)}s"
        status_str = "✅ Ready" if clip_path.exists() else "❌ Missing"
        dialogue = p.get("dialogue", "").strip() or "[ambient / no speech]"
        if len(dialogue) > 45:
            dialogue = dialogue[:42] + "..."
        print(f"{clip_name:<12} | {dur_str:<10} | {status_str:<10} | {dialogue}")

    print("=" * 80)
    print("Please inspect the generated video clips above for:")
    print("  1. Visual glitches, artifacts, or distortion")
    print("  2. Avatar likeness and consistency")
    print("  3. Lip-sync accuracy and natural facial movement")
    print("=" * 80)


def regenerate_clip(
    scene_num: int,
    prompt_adjustment: str | None,
    job_dir: Path,
    job_id: str,
    prompts_list: list[dict],
    avatar_path: Path,
    regen_image: bool = False,
) -> None:
    """Regenerates a specific clip (video, or image+video) with optional prompt tweak."""
    target_scene = None
    for p in prompts_list:
        if p.get("scene_num") == scene_num:
            target_scene = p
            break

    if not target_scene:
        print(f"❌ Error: Scene number {scene_num} not found in prompts.")
        return

    scene_id = target_scene["scene_id"]
    images_dir = job_dir / "generated_images"
    clips_dir = job_dir / "clips"
    img_dest = images_dir / f"img_{scene_num:02d}.png"
    clip_dest = clips_dir / f"clip_{scene_num:02d}.mp4"

    client = FlowClient()

    if prompt_adjustment:
        print(f"Applying prompt adjustment to {scene_id}: \"{prompt_adjustment}\"")
        target_scene["video_prompt"] = f"{target_scene['video_prompt']} {prompt_adjustment}"
        # Update prompts.json on disk
        prompts_file = job_dir / "prompts.json"
        with open(prompts_file, "w", encoding="utf-8") as f:
            json.dump(prompts_list, f, indent=2, ensure_ascii=False)

    # Image pass if requested or missing
    if regen_image or not img_dest.exists():
        print(f"🔄 Regenerating keyframe image for {scene_id} (Banana Pro 2.1)...")
        sc_path = target_scene.get("screenshot_path")
        client.generate_image_scene(
            project_name=f"Recreation_{job_id}",
            prompt=target_scene["image_prompt"],
            avatar_path=avatar_path,
            screenshot_path=Path(sc_path) if sc_path else None,
            dest_image_path=img_dest,
        )

    # Video pass
    print(f"🔄 Regenerating video clip for {scene_id} (OmniFlash 1.1, {target_scene.get('omni_duration', 4)}s)...")
    client.generate_video_scene(
        project_name=f"Recreation_{job_id}",
        prompt=target_scene["video_prompt"],
        first_frame_path=img_dest,
        dest_video_path=clip_dest,
        duration=target_scene.get("omni_duration", 4),
    )
    print(f"✅ Clip clip_{scene_num:02d}.mp4 regenerated successfully!\n")


def run_stage_5_interactive(
    job_dir: Path,
    job_id: str,
    prompts_list: list[dict],
    avatar_path: Path,
) -> bool:
    """
    Executes Stage 5: Review checkpoint loop.
    Returns True if approved, False if aborted.
    """
    clips_dir = job_dir / "clips"

    while True:
        display_review_table(clips_dir, prompts_list)
        print("\nCommands:")
        print("  approve                                - Proceed to Stage 6 (Final Edit & SRT Captions)")
        print("  regen <clip_num> [optional prompt...]  - Regenerate clip video with optional prompt tweak")
        print("  regen-both <clip_num> [optional...]    - Regenerate BOTH keyframe image and video")
        print("  abort                                  - Exit cleanly, keeping all generated files")
        print()

        try:
            user_input = input("Enter command > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborting by user request.")
            return False

        if not user_input:
            continue

        cmd_lower = user_input.lower()
        if cmd_lower == "approve":
            print("\n✅ Human inspection APPROVED! Proceeding to Stage 6 Final Edit...")
            return True
        elif cmd_lower == "abort":
            print("\n🛑 Pipeline ABORTED by user. All generated files remain intact in the job directory.")
            return False
        elif cmd_lower.startswith("regen"):
            parts = user_input.split(maxsplit=2)
            cmd_type = parts[0].lower()
            if len(parts) < 2 or not parts[1].isdigit():
                print("❌ Usage: regen <clip_number> [optional prompt adjustment]")
                continue
            clip_num = int(parts[1])
            prompt_adj = parts[2] if len(parts) > 2 else None
            regen_img = cmd_type == "regen-both"

            try:
                regenerate_clip(
                    scene_num=clip_num,
                    prompt_adjustment=prompt_adj,
                    job_dir=job_dir,
                    job_id=job_id,
                    prompts_list=prompts_list,
                    avatar_path=avatar_path,
                    regen_image=regen_img,
                )
            except Exception as e:
                print(f"❌ Regeneration failed: {e}")
        else:
            print(f"❌ Unknown command: '{user_input}'. Type 'approve', 'regen <num>', or 'abort'.")
