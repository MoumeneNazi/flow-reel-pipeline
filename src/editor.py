"""
Stage 6 — Final Edit & SRT Captions:
Concatenates approved clips using FFmpeg (1080x1920, 30fps, CRF 18, preset slow, AAC 192k),
generates frame-accurate SRT subtitles using faster-whisper, and burns in captions if requested.
"""

from pathlib import Path
import json
import logging
import re
import subprocess
import sys

from .config import (
    FINAL_WIDTH,
    FINAL_HEIGHT,
    FINAL_FPS,
    VIDEO_CODEC,
    CRF_VALUE,
    PRESET,
    AUDIO_CODEC,
    AUDIO_BITRATE,
)

logger = logging.getLogger("flow_reel.editor")


def format_srt_time(seconds: float) -> str:
    """Converts seconds float to SRT timecode format: HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        millis = 999
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def clip_has_audio(clip_path: Path) -> bool:
    """Checks if a video clip contains an audio stream using ffprobe."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(clip_path),
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True)
        return "audio" in res.stdout.lower()
    except Exception:
        return True


def ensure_clip_audio(clip_path: Path, work_dir: Path) -> Path:
    """If clip has no audio track, adds a silent AAC track so concat filter succeeds."""
    if clip_has_audio(clip_path):
        return clip_path

    logger.warning(f"Clip {clip_path.name} lacks audio; generating silent audio track...")
    fixed_path = work_dir / f"with_audio_{clip_path.name}"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(clip_path),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:v",
        "copy",
        "-c:a",
        AUDIO_CODEC,
        "-shortest",
        str(fixed_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return fixed_path


def concatenate_clips(
    clip_paths: list[Path],
    output_path: Path,
    work_dir: Path,
) -> Path:
    """
    Concatenates clips in order using FFmpeg.
    Normalizes every clip to:
      - 1080x1920 resolution (decrease scale + pad to maintain aspect ratio)
      - 30 fps
      - libx264, -crf 18, -preset slow
      - AAC audio at 192k, 48000Hz stereo
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    if not clip_paths:
        raise ValueError("No video clips provided for concatenation.")

    logger.info(f"Preparing to concatenate {len(clip_paths)} clips into {output_path}...")

    # Ensure all clips have audio
    safe_clips = [ensure_clip_audio(cp, work_dir) for cp in clip_paths]

    # Build FFmpeg filter_complex
    input_args = []
    filter_parts = []
    concat_inputs = []

    for idx, cp in enumerate(safe_clips):
        input_args.extend(["-i", str(cp)])
        v_in = f"[{idx}:v]"
        a_in = f"[{idx}:a]"
        v_out = f"[v{idx}]"
        a_out = f"[a{idx}]"

        # Scale to fit 1080x1920, pad, set 30fps
        v_filter = (
            f"{v_in}scale={FINAL_WIDTH}:{FINAL_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={FINAL_WIDTH}:{FINAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={FINAL_FPS}{v_out}"
        )
        a_filter = (
            f"{a_in}aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo{a_out}"
        )
        filter_parts.append(v_filter)
        filter_parts.append(a_filter)
        concat_inputs.append(f"{v_out}{a_out}")

    concat_cmd = "".join(concat_inputs) + f"concat=n={len(safe_clips)}:v=1:a=1[outv][outa]"
    filter_parts.append(concat_cmd)
    full_filter = "; ".join(filter_parts)

    cmd = (
        ["ffmpeg", "-y"]
        + input_args
        + [
            "-filter_complex",
            full_filter,
            "-map",
            "[outv]",
            "-map",
            "[outa]",
            "-c:v",
            VIDEO_CODEC,
            "-crf",
            str(CRF_VALUE),
            "-preset",
            PRESET,
            "-c:a",
            AUDIO_CODEC,
            "-b:a",
            AUDIO_BITRATE,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )

    logger.info(f"Executing FFmpeg concatenation and normalization command...")
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"FFmpeg concatenation failed: {res.stderr}")
        raise RuntimeError(f"FFmpeg failed with exit code {res.returncode}: {res.stderr}")

    logger.info(f"Final reel successfully created at {output_path}")
    return output_path


def generate_srt_from_final_video(
    final_video_path: Path,
    output_srt_path: Path,
    model_size: str = "base",
) -> Path:
    """
    Runs faster-whisper directly on final_reel.mp4 (not source video)
    to generate frame-accurate SRT subtitles.
    """
    logger.info(f"Generating frame-accurate SRT for {final_video_path}...")
    output_srt_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from .ingest import get_whisper_model
        model = get_whisper_model(model_size)
    except Exception as e:
        logger.error(f"Failed to load Whisper model: {e}")
        raise
    segments_iter, info = model.transcribe(
        str(final_video_path),
        word_timestamps=True,
        vad_filter=True,
    )

    srt_entries = []
    entry_idx = 1

    for seg in segments_iter:
        text = seg.text.strip()
        if not text:
            continue
        start_tc = format_srt_time(seg.start)
        end_tc = format_srt_time(seg.end)

        srt_entry = f"{entry_idx}\n{start_tc} --> {end_tc}\n{text}\n"
        srt_entries.append(srt_entry)
        entry_idx += 1

    with open(output_srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_entries) + "\n")

    logger.info(f"Frame-accurate SRT generated ({len(srt_entries)} entries) -> {output_srt_path}")
    return output_srt_path


def burn_captions_into_video(
    video_path: Path,
    srt_path: Path,
    output_captioned_path: Path,
) -> Path:
    """
    Burns SRT captions into the video using FFmpeg subtitles filter.
    Styled with modern short-form bold captions centered near bottom.
    """
    logger.info(f"Burning captions into {output_captioned_path}...")
    output_captioned_path.parent.mkdir(parents=True, exist_ok=True)

    # Check if SRT has content
    if not srt_path.exists() or srt_path.stat().st_size <= 5:
        logger.warning("SRT file is empty or contains no spoken lines; copying video directly.")
        import shutil
        shutil.copy2(video_path, output_captioned_path)
        return output_captioned_path

    srt_dir = srt_path.resolve().parent
    srt_name = srt_path.name
    input_video_name = video_path.resolve().name
    output_abs = str(output_captioned_path.resolve())

    style = (
        "FontName=Arial,FontSize=22,Bold=1,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=3,Outline=2.5,Shadow=1.5,"
        "Alignment=2,MarginV=65"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_video_name,
        "-vf",
        f"subtitles={srt_name}:force_style='{style}'",
        "-c:v",
        VIDEO_CODEC,
        "-crf",
        str(CRF_VALUE),
        "-preset",
        PRESET,
        "-c:a",
        "copy",
        output_abs,
    ]

    res = subprocess.run(cmd, cwd=str(srt_dir), capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"FFmpeg caption burn failed: {res.stderr}")
        raise RuntimeError(f"Failed burning captions into video: {res.stderr}")

    logger.info(f"Captioned video saved successfully to {output_captioned_path}")
    return output_captioned_path


def run_stage_6(
    job_dir: Path,
    burn_captions: bool = False,
    model_size: str = "base",
    caption_style: str = "yellow-pop",
    caption_position: str = "lower-third",
) -> dict[str, Path]:
    """
    Executes Stage 6: Concatenation, Normalization, SRT extraction, and optional Viral Pop Caption burn.
    Returns dict with paths to final_reel.mp4, final_reel.srt, and optionally final_reel_captioned.mp4.
    """
    from .captions import (
        extract_words_from_whisper,
        generate_pop_subtitles_ass,
        burn_pop_captions_into_video,
    )
    from .ingest import get_whisper_model

    clips_dir = job_dir / "clips"
    final_dir = job_dir / "final"
    work_dir = job_dir / "temp_edit"

    clip_files = sorted(
        clips_dir.glob("clip_*.mp4"),
        key=lambda p: int(re.search(r"\d+", p.stem).group(0)) if re.search(r"\d+", p.stem) else 0,
    )

    if not clip_files:
        raise FileNotFoundError(f"No clips found in {clips_dir} to stitch.")

    final_reel = final_dir / "final_reel.mp4"
    final_srt = final_dir / "final_reel.srt"
    final_ass = final_dir / "final_reel.ass"

    # 1. Concatenate & normalize
    concatenate_clips(clip_files, final_reel, work_dir)

    # 2. Extract word-accurate transcription using faster-whisper
    logger.info(f"Transcribing {final_reel} with faster-whisper (model={model_size}) for frame-accurate captions...")
    model = get_whisper_model(model_size)
    segments_iter, info = model.transcribe(
        str(final_reel),
        word_timestamps=True,
        vad_filter=True,
    )
    segments = list(segments_iter)

    # Write standard SRT
    srt_entries = []
    for idx, seg in enumerate(segments, start=1):
        txt = seg.text.strip()
        if not txt:
            continue
        srt_entries.append(f"{idx}\n{format_srt_time(seg.start)} --> {format_srt_time(seg.end)}\n{txt}\n")
    with open(final_srt, "w", encoding="utf-8") as f:
        f.write("\n".join(srt_entries) + "\n")
    logger.info(f"Saved standard SRT ({len(srt_entries)} entries) -> {final_srt}")

    results = {
        "final_reel": final_reel,
        "final_srt": final_srt,
    }

    # 3. Optional burned viral yellow-pop captions
    if burn_captions:
        captioned_reel = final_dir / "final_reel_captioned.mp4"
        words = extract_words_from_whisper(segments)
        if words:
            generate_pop_subtitles_ass(
                words=words,
                output_path=final_ass,
                style=caption_style,
                position=caption_position,
                font_name="Arial Black",
                font_size=28,
                max_words_per_screen=3,
                uppercase=True,
            )
            burn_pop_captions_into_video(final_reel, final_ass, captioned_reel)
        else:
            # Fallback if no words detected
            import shutil
            shutil.copy2(final_reel, captioned_reel)
        results["final_captioned"] = captioned_reel
        results["final_ass"] = final_ass

    return results
