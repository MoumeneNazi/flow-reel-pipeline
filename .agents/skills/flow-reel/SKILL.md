---
name: flow-reel
description: >-
  Automated video recreation pipeline using Google Flow (Banana Pro 2.1 & OmniFlash 1.1)
  via the GTA1 MCP Flow server. Ingests video, extracts scenes & dialogue, generates avatar
  keyframe images and lip-synced video clips, stops at human review checkpoint, and stitches final video with SRT captions.
---

# Flow Reel Recreation Agent & Workflow

This skill provides an end-to-end automated pipeline to recreate any reference video using an AI avatar in Google Flow (Banana Pro 2.1 for images → OmniFlash 1.1 for video clips) and GTA1 MCP Flow tools.

## Architecture & Stages

1. **Stage 1 — Ingest & Transcribe**: Uses `yt-dlp` to download high-resolution source video and `faster-whisper` with word-level timestamps (`transcript.json`).
2. **Stage 2 — Transition Screenshots**: Uses `PySceneDetect` (`ContentDetector`, default threshold 27.0) to detect transitions, saving middle-frame screenshots (`scenes/scene_01.jpg`) and `scenes.csv`.
3. **Stage 3 — Scene-to-Prompt Mapping**: Aligns transcript words with each scene's time window, generating vertical 9:16 prompts in `prompts.json` (Banana Pro 2.1 image prompt + OmniFlash 1.1 video prompt following the Flow playbook).
4. **Stage 4 — Google Flow Generation**:
   - Sequential generation with configurable cooldown (default 15s).
   - **Image Pass:** Banana Pro 2.1 (`flow_generate`, `type: "image"`, reference image avatar) -> `generated_images/img_01.png`.
   - **Video Pass:** OmniFlash 1.1 (`flow_generate`, `type: "video"`, `first_frame: img_01.png`, `download_quality: "upscaled"`) -> `clips/clip_01.mp4`.
   - Continuous state persistence in `state.json` to allow resume on failure.
5. **Stage 5 — MANDATORY Human-in-the-Loop Review Checkpoint**: Halts before FFmpeg stitching. Prints clip paths and dialogue, prompting for `approve`, `regen <clip_num> [prompt]`, or `abort`.
6. **Stage 6 — Final Edit & SRT Captions**: Concatenates approved clips using FFmpeg (1080x1920, 30fps, `libx264 -crf 18 -preset slow`, AAC 192k) into `final_reel.mp4`, runs `faster-whisper` on the final video for frame-accurate `final_reel.srt`, and optionally creates `final_reel_captioned.mp4`.

## Running the Pipeline

### Single Command CLI
```bash
# Dry run to verify scene detection and prompts without spending credits:
python run_reel.py "<VIDEO_URL_OR_PATH>" --dry-run

# Full recreation with AI avatar:
python run_reel.py "<VIDEO_URL_OR_PATH>" --avatar "./avatar.png"

# Full recreation with burned-in subtitles:
python run_reel.py "<VIDEO_URL_OR_PATH>" --avatar "./avatar.png" --burn-captions
```

### Review Checkpoint Commands
During Stage 5 review:
- `approve`: Proceed to Stage 6 stitching and caption generation.
- `regen <clip_number> [prompt adjustment]`: Regenerates that video clip with prompt tweak.
- `regen-both <clip_number> [prompt adjustment]`: Regenerates both keyframe image and video.
- `abort`: Halt cleanly, preserving all generated files.
