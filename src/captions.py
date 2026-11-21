"""
Advanced Captioning Engine:
Generates dynamic Advanced SubStation Alpha (.ass) subtitles with word-by-word pop animation,
where the active spoken word lights up in vibrant yellow (#FFE135) with scale bounce,
and surrounding words remain crisp white with a thick black outline.
"""

from pathlib import Path
import re
import logging
import subprocess
import shutil
from typing import Optional

logger = logging.getLogger("flow_reel.captions")

# Color mapping in ASS format: &H<Alpha><Blue><Green><Red>&
# Hex #FFE135 -> R=FF (255), G=E1 (225), B=35 (53) -> ASS BGR: &H0035E1FF&
COLOR_PALETTES = {
    "yellow-pop": {
        "name": "Yellow Pop (TikTok / Shorts)",
        "active_color": "&H0035E1FF&",   # Bright Pop Yellow #FFE135
        "inactive_color": "&H00FFFFFF&", # Clean White #FFFFFF
        "outline_color": "&H00000000&",  # Jet Black outline
        "shadow_color": "&H80000000&",   # Semi-transparent drop shadow
    },
    "green-pop": {
        "name": "Neon Green Pop",
        "active_color": "&H005EC522&",   # Neon Green #22C55E
        "inactive_color": "&H00FFFFFF&",
        "outline_color": "&H00000000&",
        "shadow_color": "&H80000000&",
    },
    "cyan-pop": {
        "name": "Electric Cyan Pop",
        "active_color": "&H00D4B606&",   # Electric Cyan #06B6D4
        "inactive_color": "&H00FFFFFF&",
        "outline_color": "&H00000000&",
        "shadow_color": "&H80000000&",
    },
    "clean-white": {
        "name": "Clean White",
        "active_color": "&H00FFFFFF&",
        "inactive_color": "&H00C0C0C0&",
        "outline_color": "&H00000000&",
        "shadow_color": "&H80000000&",
    },
}

# Position configurations for 1080x1920 (9:16 vertical)
POSITIONS = {
    "lower-third": {"alignment": 2, "margin_v": 380},  # ~70% down, safe above caption bars & UI controls
    "middle": {"alignment": 5, "margin_v": 0},         # Dead center
    "bottom": {"alignment": 2, "margin_v": 160},       # Bottom edge
}


def format_ass_time(seconds: float) -> str:
    """Formats seconds float to ASS timestamp: H:MM:SS.cs (centiseconds)."""
    if seconds < 0:
        seconds = 0.0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def sanitize_word(text: str, uppercase: bool = True) -> str:
    """Sanitizes word token, strips extraneous punctuation for clean punchy subtitles."""
    cleaned = text.strip()
    cleaned = re.sub(r'^[“"\'\(\[\{]+', '', cleaned)
    cleaned = re.sub(r'[,;:\."\'\)\]\}]+$', '', cleaned)
    if uppercase:
        cleaned = cleaned.upper()
    return cleaned


def extract_words_from_whisper(segments) -> list[dict]:
    """
    Extracts word-level timestamps from faster-whisper transcription segments.
    Returns list of dicts: [{'text': str, 'start': float, 'end': float}, ...]
    """
    words = []
    for seg in segments:
        if hasattr(seg, "words") and seg.words:
            for w in seg.words:
                text = w.word.strip()
                if text:
                    words.append({
                        "text": text,
                        "start": float(w.start),
                        "end": float(w.end),
                        "probability": getattr(w, "probability", 1.0),
                    })
        else:
            seg_text = seg.text.strip()
            if seg_text:
                words.append({
                    "text": seg_text,
                    "start": float(seg.start),
                    "end": float(seg.end),
                    "probability": 1.0,
                })
    return words


def chunk_words(
    words: list[dict],
    max_words: int = 3,
    max_pause_sec: float = 0.45,
    uppercase: bool = True,
) -> list[list[dict]]:
    """
    Groups words into short, readable chunks (default 3 words per screen).
    Breaks naturally on question marks, exclamation marks, or speaking pauses.
    """
    chunks = []
    current_chunk = []

    for w in words:
        text = w.get("text", "").strip()
        if not text:
            continue

        start = float(w.get("start", 0.0))
        end = float(w.get("end", start + 0.1))

        if current_chunk:
            prev_end = current_chunk[-1]["end"]
            pause = start - prev_end
            prev_raw = current_chunk[-1]["text"].strip()

            is_sentence_end = prev_raw.endswith("?") or prev_raw.endswith("!")
            if len(current_chunk) >= max_words or pause > max_pause_sec or is_sentence_end:
                chunks.append(current_chunk)
                current_chunk = []

        clean_w = dict(w)
        clean_w["start"] = start
        clean_w["end"] = end
        clean_w["clean_text"] = sanitize_word(text, uppercase=uppercase)
        current_chunk.append(clean_w)

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def build_ass_header(
    font_name: str = "Arial Black",
    font_size: int = 28,
    palette_name: str = "yellow-pop",
    position_name: str = "lower-third",
    video_width: int = 1080,
    video_height: int = 1920,
) -> str:
    """Constructs the ASS script header and style definitions."""
    palette = COLOR_PALETTES.get(palette_name, COLOR_PALETTES["yellow-pop"])
    pos = POSITIONS.get(position_name, POSITIONS["lower-third"])

    header = f"""[Script Info]
Title: FlowReel Viral Pop Captions
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {video_width}
PlayResY: {video_height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: PopBase,{font_name},{font_size * 2},{palette['inactive_color']},&H000000FF&,{palette['outline_color']},{palette['shadow_color']},-1,0,0,0,100,100,2,0,1,8,3,{pos['alignment']},40,40,{pos['margin_v']},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header


def generate_pop_subtitles_ass(
    words: list[dict],
    output_path: Path,
    style: str = "yellow-pop",
    position: str = "lower-third",
    font_name: str = "Arial Black",
    font_size: int = 28,
    max_words_per_screen: int = 3,
    uppercase: bool = True,
    lead_seconds: float = 0.08,
    video_width: int = 1080,
    video_height: int = 1920,
) -> Path:
    """
    Generates Advanced SubStation Alpha (.ass) subtitles where:
    - Text is shown in chunks of 2-4 words.
    - Each word pops in active_color (yellow #FFE135) with scale bounce while spoken.
    - Other words in the chunk are rendered in clean white with black stroke.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    palette = COLOR_PALETTES.get(style, COLOR_PALETTES["yellow-pop"])
    active_col = palette["active_color"]
    inactive_col = palette["inactive_color"]

    chunks = chunk_words(words, max_words=max_words_per_screen, uppercase=uppercase)
    events = []

    for chunk in chunks:
        chunk_words_count = len(chunk)
        chunk_start = max(0.0, chunk[0]["start"] - lead_seconds)
        chunk_end = max(chunk_start + 0.2, chunk[-1]["end"])

        for i, active_w in enumerate(chunk):
            w_start = max(chunk_start, active_w["start"] - lead_seconds)

            if i + 1 < chunk_words_count:
                w_end = max(w_start + 0.08, chunk[i + 1]["start"] - lead_seconds)
            else:
                w_end = chunk_end

            if w_end <= w_start:
                w_end = w_start + 0.15

            start_tc = format_ass_time(w_start)
            end_tc = format_ass_time(w_end)

            line_parts = []
            for j, w in enumerate(chunk):
                word_str = w["clean_text"]
                if j == i:
                    # Active word: Yellow pop (#FFE135) + 115% scale bounce
                    line_parts.append(f"{{\\c{active_col}\\fscx115\\fscy115}}{word_str}{{\\c{inactive_col}\\fscx100\\fscy100}}")
                else:
                    line_parts.append(word_str)

            line_text = " ".join(line_parts)
            events.append(f"Dialogue: 0,{start_tc},{end_tc},PopBase,,0,0,0,,{line_text}")

    full_ass = build_ass_header(
        font_name=font_name,
        font_size=font_size,
        palette_name=style,
        position_name=position,
        video_width=video_width,
        video_height=video_height,
    ) + "\n".join(events) + "\n"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(full_ass)

    logger.info(f"Generated dynamic pop ASS subtitles ({len(events)} events) -> {output_path}")
    return output_path


def burn_pop_captions_into_video(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
    crf: int = 18,
    preset: str = "slow",
) -> Path:
    """
    Burns .ass subtitles into video using FFmpeg libass filter.
    Preserves exact audio track and normalizes video stream.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not ass_path.exists() or ass_path.stat().st_size <= 50:
        logger.warning(f"ASS subtitle file {ass_path} is empty; copying video without burning.")
        shutil.copy2(video_path, output_path)
        return output_path

    ass_dir = ass_path.resolve().parent
    ass_name = ass_path.name
    video_abs = str(video_path.resolve())
    output_abs = str(output_path.resolve())

    logger.info(f"Burning dynamic pop captions into {output_path} via FFmpeg libass...")

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_abs,
        "-vf",
        f"ass={ass_name}",
        "-c:v",
        "libx264",
        "-crf",
        str(crf),
        "-preset",
        preset,
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        output_abs,
    ]

    res = subprocess.run(cmd, cwd=str(ass_dir), capture_output=True, text=True)
    if res.returncode != 0:
        logger.error(f"FFmpeg caption burn failed: {res.stderr}")
        raise RuntimeError(f"FFmpeg caption burn failed: {res.stderr}")

    logger.info(f"Successfully burned captions -> {output_path}")
    return output_path
