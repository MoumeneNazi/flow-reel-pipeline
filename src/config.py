"""
Configuration defaults and constants for the Flow Reel Recreation Pipeline.
"""

from pathlib import Path
import os

# Default models
DEFAULT_IMAGE_MODEL = "Nano Banana Pro"
DEFAULT_VIDEO_MODEL = "Omni 1.1 Flash"

# PySceneDetect defaults
DEFAULT_SCENE_THRESHOLD = 27.0
DEFAULT_MIN_SCENE_LEN_SEC = 1.0

# Google Flow timing and rate limit defaults
DEFAULT_COOLDOWN_SEC = 15.0
DEFAULT_MAX_CREDITS = 25

# Final Edit & Normalization constants
FINAL_WIDTH = 1080
FINAL_HEIGHT = 1920
FINAL_FPS = 30
VIDEO_CODEC = "libx264"
CRF_VALUE = 18
PRESET = "slow"
AUDIO_CODEC = "aac"
AUDIO_BITRATE = "192k"

# Flow MCP HTTP API Configuration
FLOW_STUDIO_URL = os.environ.get("FLOW_STUDIO_URL", "http://127.0.0.1:8787")
FLOW_TOKEN_FILE = Path(os.environ.get("FLOW_TOKEN_FILE", Path.home() / ".flow-mcp" / "token"))
FLOW_OUTPUT_ROOT = Path(os.environ.get("FLOW_OUTPUT_ROOT", Path.home() / "flow-mcp-out"))

# Valid Omni video durations (seconds) in Google Flow
VALID_OMNI_DURATIONS = [4, 6, 8, 10]


def normalize_video_model(model_name: str) -> str:
    """
    Normalizes video model names to match Google Flow's exact UI menu item.
    Accepts: 'OmniFLash 1.1', 'OmniFlash 1.1', 'OmniFlash', 'Omni 1.1 Flash', etc.
    """
    clean = model_name.strip()
    lower = clean.lower().replace(" ", "").replace("-", "")
    if "omniflash" in lower or "omni1.1" in lower:
        return "Omni 1.1 Flash"
    if "veo" in lower:
        if "fast" in lower:
            return "Veo 3.1 - Fast"
        return "Veo 3.1 - Quality"
    return clean


def normalize_image_model(model_name: str) -> str:
    """
    Normalizes image model names to match Google Flow's exact UI menu item.
    Accepts: 'Banana Pro 2.1', 'Banana Pro', 'Nano Banana Pro', etc.
    """
    clean = model_name.strip()
    lower = clean.lower().replace(" ", "")
    if "banana" in lower:
        return "Nano Banana Pro"
    return clean


def get_flow_token() -> str:
    """Read the flow-mcp authentication token."""
    if not FLOW_TOKEN_FILE.exists():
        raise FileNotFoundError(
            f"Flow MCP token file not found at '{FLOW_TOKEN_FILE}'. "
            "Please ensure the Flow MCP server / Studio is running."
        )
    return FLOW_TOKEN_FILE.read_text(encoding="utf-8").strip()


def pick_omni_duration(target_duration: float, dialogue: str = "") -> int:
    """
    Dynamically selects the optimal OmniFlash 1.1 duration (4s, 6s, 8s, 10s)
    based on both scene duration and spoken dialogue content.
    Ensures spoken dialogue is never clipped or rushed.
    """
    # Estimate speech time from dialogue (average ~2.4 words per second)
    words = [w for w in dialogue.split() if w.strip()]
    speech_est_sec = len(words) / 2.4 if words else 0.0

    # Needed duration is at least as long as the speech or original scene
    needed = max(target_duration, speech_est_sec)

    if needed <= 4.8:
        return 4
    elif needed <= 6.8:
        return 6
    elif needed <= 8.8:
        return 8
    else:
        return 10
