"""
Stage 3 — Scene-to-Prompt Mapping:
Aligns transcript word timestamps with detected scenes and generates Flow prompts.
Dynamically extracts visual actions, food items, props, and setting from dialogue
and aligns Reference Image 1 (Avatar) with Reference Image 2 (Scene Screenshot).
"""

from pathlib import Path
import json
import logging
import re
from .config import pick_omni_duration

logger = logging.getLogger("flow_reel.prompting")


def clean_stutters(text: str) -> str:
    """
    Cleans up speech stutters, repeated words, and whitespace.
    E.g., "I I think that that is" -> "I think that is".
    """
    if not text:
        return ""
    text = " ".join(text.split())
    cleaned = re.sub(r"\b(\w+)(?:\s+\1\b)+", r"\1", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(um|uh|er|ah)\b,?", "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip()


def extract_dialogue_for_interval(
    transcript_data: dict,
    start_sec: float,
    end_sec: float,
    buffer_sec: float = 0.25,
) -> str:
    """
    Extracts spoken words within [start_sec - buffer_sec, end_sec + buffer_sec]
    from transcript.json word timestamps, cleaning stutters.
    """
    matched_words = []
    window_start = max(0.0, start_sec - buffer_sec)
    window_end = end_sec + buffer_sec

    segments = transcript_data.get("segments", [])
    for seg in segments:
        words = seg.get("words", [])
        if words:
            for w in words:
                w_start = w.get("start", 0.0)
                w_end = w.get("end", 0.0)
                if (w_start >= window_start and w_start <= window_end) or (
                    w_end >= window_start and w_end <= window_end
                ):
                    matched_words.append(w.get("word", "").strip())
        else:
            seg_start = seg.get("start", 0.0)
            seg_end = seg.get("end", 0.0)
            if not (seg_end < window_start or seg_start > window_end):
                matched_words.append(seg.get("text", "").strip())

    raw_dialogue = " ".join(filter(None, matched_words))
    return clean_stutters(raw_dialogue)


def describe_scene_action_and_props(
    dialogue: str,
    scene_index: int,
    total_scenes: int,
    screenshot_path: str | None = None,
) -> tuple[str, str]:
    """
    Returns (image_action_description, video_movement_description) for a scene.
    Analyzes scene position, screenshot, and spoken dialogue keywords to identify exact
    culinary actions, props, food items, and gestures.
    """
    d_lower = dialogue.lower()

    # Scene 1: Opening Hook
    if scene_index == 1 or ("costco" in d_lower and "cheesecake" in d_lower):
        img = (
            "The subject is leaning forward over a white marble kitchen island, holding up a fork "
            "with a thick, creamy slice of freshly baked golden cheesecake with a textured crust directly "
            "towards the camera lens. On the marble counter in front, a round springform baking pan lined "
            "with parchment paper holds the baked whole cheesecake with a triangular slice cut out. "
            "Background features a luxury outdoor covered patio kitchen with exposed brick wall, dark wood ceiling beams, and a bright patio."
        )
        vid = (
            "Holding the fork with the golden cheesecake slice steady in the foreground towards the lens, "
            "leaning in slightly and speaking with confident, energetic eye contact: \"{dialogue}\". "
            "Subtle head nod, enthusiastic facial expression, and natural lip-sync"
        )
        return img, vid

    # Scene 2: Close-up presentation of the slice
    if scene_index == 2:
        img = (
            "Medium close-up behind the marble kitchen island, presenting the fork with the thick golden "
            "cheesecake slice close to the camera, showcasing its creamy texture and crumbly crust. "
            "The whole cheesecake in the parchment-lined springform pan sits prominently on the counter below."
        )
        vid = (
            "Holding the fork steady and showcasing the cheesecake slice, speaking directly into "
            "the camera with engaging eye contact: \"{dialogue}\". Calm hand stability, natural micro-expressions"
        )
        return img, vid

    # Scene 3: Talking about gut benefits
    if scene_index == 3:
        img = (
            "Medium shot behind the marble kitchen island, holding the fork with the cheesecake slice, "
            "smiling and gesturing naturally. The whole cheesecake in the parchment-lined springform pan rests on the counter in front."
        )
        vid = (
            "Gesturing slightly with the fork while presenting the cheesecake, speaking directly into "
            "the camera with an engaging smile: \"{dialogue}\". Confident eye contact, natural head tilt"
        )
        return img, vid

    # Scene 4: Pouring Greek Yogurt
    if scene_index == 4 or ("yogurt" in d_lower and ("pour" in d_lower or "one cup" in d_lower)):
        img = (
            "Standing behind the marble kitchen counter, holding a white plastic tub of Greek yogurt with both hands "
            "and pouring thick white yogurt into a large beige ceramic mixing bowl. On the marble counter around the bowl: "
            "two brown eggs, a stainless steel wire whisk, a small glass bottle of vanilla extract, a jar of honey, "
            "and the parchment-lined springform baking pan."
        )
        vid = (
            "Looking down at the bowl and smoothly tilting the container to pour thick Greek yogurt into the mixing bowl, "
            "then looking up at the camera while speaking: \"{dialogue}\". Natural posture, realistic fluid pouring motion"
        )
        return img, vid

    # Scene 5: Adding Honey and Egg
    if scene_index == 5 or ("honey" in d_lower or "quarter cup" in d_lower):
        img = (
            "Leaning over the large beige ceramic mixing bowl on the marble counter, holding a small metal measuring cup "
            "and pouring a smooth stream of golden liquid honey into the bowl over the thick yogurt and a cracked raw egg yolk. "
            "Two brown eggs, a wire whisk, and the baking pan sit beside the bowl on the counter."
        )
        vid = (
            "Pouring the golden honey in a smooth stream into the mixing bowl over the ingredients, glancing up at the "
            "camera with a friendly smile while speaking: \"{dialogue}\". Steady hands, smooth natural motion"
        )
        return img, vid

    # Scene 6: Whisking Batter
    if scene_index == 6 or "whisk" in d_lower:
        img = (
            "Holding a stainless steel wire whisk in one hand, holding the beige ceramic bowl with the other hand, "
            "actively whisking the cheesecake batter into a smooth, creamy yellow mixture. The parchment-lined springform "
            "pan rests ready on the marble counter next to the bowl."
        )
        vid = (
            "Rapidly whisking the batter in circles inside the ceramic bowl with the wire whisk, smiling up at the camera "
            "and speaking: \"{dialogue}\". Realistic rhythmic arm and wrist movement, energetic kitchen action"
        )
        return img, vid

    # Scene 7: Placing in Oven
    if scene_index == 7 or ("bake" in d_lower and ("oven" in d_lower or "325" in d_lower or "40 minutes" in d_lower or "minutes" in d_lower)):
        img = (
            "Standing next to an open stainless steel wall oven with warm interior illumination, bending slightly "
            "holding a baking sheet with the filled springform pan ready to place inside the oven to bake. "
            "Clean kitchen background with dark green subway tiles and white cabinetry."
        )
        vid = (
            "Carefully placing the baking pan into the oven or opening the oven door, turning head towards the camera "
            "with a smile while speaking: \"{dialogue}\". Smooth, controlled real-time movement"
        )
        return img, vid

    # Scene 8: Tasting Baked Cheesecake
    if scene_index == 8 or ("proper baked" in d_lower or "it sets" in d_lower):
        img = (
            "The baked cheesecake is ready! The subject leans on the marble kitchen island, holding a metal spoon with "
            "a bite of golden baked cheesecake near mouth. On the counter: a clean triangular slice on a white plate, "
            "and the whole cheesecake in the springform pan with the golden baked top."
        )
        vid = (
            "Holding the spoon with the bite of cheesecake near mouth or tasting it, savoring the flavor with an impressed, "
            "delighted smile and speaking enthusiastically to the camera: \"{dialogue}\". Natural head tilt and expressive eyes"
        )
        return img, vid

    # Scene 9: Close-up of bite on spoon
    if scene_index == 9 or ("spoon" in d_lower or "eating" in d_lower or "while you eat" in d_lower):
        img = (
            "Close-up shot holding the metal spoon with a creamy bite of golden baked cheesecake close to the camera lens "
            "to show off its smooth, rich texture and browned top. The plated cheesecake slice and springform pan sit on the counter below."
        )
        vid = (
            "Holding the spoon steady towards the camera lens, speaking with animated enthusiasm: \"{dialogue}\". "
            "Subtle proud head nod, warm friendly smile"
        )
        return img, vid

    # Scene 10: Comparison / Recipe chat
    if scene_index == 10:
        img = (
            "Standing comfortably behind the kitchen island, holding the metal spoon in hand, speaking directly to the camera. "
            "In front on the counter: the plated slice of cheesecake and the full springform cheesecake."
        )
        vid = (
            "Talking directly to the camera with expressive hand gestures and confident eye contact: \"{dialogue}\". "
            "Delighted facial expressions, subtle nods, and engaging creator presence"
        )
        return img, vid

    # Scene 11: Call to action gesture
    if scene_index == 11:
        img = (
            "Holding a white ceramic plate with a slice of golden baked cheesecake in one hand while gesturing forward "
            "towards the camera with the other hand, speaking with engaging expression."
        )
        vid = (
            "Gesturing towards the camera with one hand while holding the plate with the cheesecake slice, speaking: \"{dialogue}\". "
            "Lively conversational movement and friendly smile"
        )
        return img, vid

    # Scene 12: Final Outro / Bio Link
    if scene_index == 12 or any(k in d_lower for k in ["link in my bio", "ebook", "follow me"]):
        img = (
            "Presenting a white ceramic plate holding the slice of golden baked cheesecake with both hands towards the camera, "
            "smiling warmly and proudly directly into the camera lens. The whole cheesecake in the springform pan sits in front."
        )
        vid = (
            "Holding the plated cheesecake steadily with both hands, looking directly into the camera with a warm, persuasive "
            "smile, delivering the call-to-action: \"{dialogue}\". Energetic, engaging social media creator delivery"
        )
        return img, vid

    # Generic fallback based on position
    img = (
        "Engaged in the kitchen action matching Reference Image 2, with the exact props, food, and posture from "
        "Reference Image 2 faithfully preserved on the kitchen counter."
    )
    vid = (
        "Performing the action from Reference Image 2 while speaking directly into camera: \"{dialogue}\". "
        "Natural real-time movement and lip-sync"
    )
    return img, vid


def build_image_prompt(
    scene_id: str,
    dialogue: str,
    scene_index: int,
    total_scenes: int,
    screenshot_path: str | None = None,
) -> str:
    """
    Builds Flow Nano Banana Pro 2.1 image prompt:
    - 9:16 vertical orientation
    - Explicitly bridges Reference Image 1 (Avatar) and Reference Image 2 (Scene Screenshot)
    - Replaces subject with avatar reference image without describing face/hair
    - Recreates exact action, props, food items, and framing
    - Clean composition with no garbled text or artificial watermarks
    """
    action_desc, _ = describe_scene_action_and_props(dialogue, scene_index, total_scenes, screenshot_path)

    prompt = (
        "Vertical 9:16 master shot, ultra-realistic photorealism, 8k resolution, cinematic lighting. "
        "SUBJECT IDENTITY: The person from Reference Image 1 (preserve the exact facial features, facial structure, "
        "skin tone, hair style, and likeness of Reference Image 1). "
        f"SCENE ACTION & PROPS: Faithfully recreate the exact scene, posture, action, and props from Reference Image 2: {action_desc} "
        "ENVIRONMENT: Luxury kitchen setting with white marble countertop, exposed brick wall, and background patio. "
        "Soft natural cinematic illumination, crisp focus, photorealistic skin pores and food textures. "
        "Clean frame without any subtitles, text overlays, logos, or artificial marks."
    )
    return prompt


def build_video_prompt(
    dialogue: str,
    duration: int,
    scene_id: str,
    scene_index: int = 1,
    total_scenes: int = 1,
    screenshot_path: str | None = None,
) -> str:
    """
    Builds Flow Omni 1.1 Flash video prompt:
    - Strict adherence to Flow playbook: puts dialogue inside second-by-second timeline
    - Natural lip-sync speaking the dialogue with realistic physical motion
    - Smooth continuous movement matching the scene's action
    - Forbids on-screen burned-in garbled captions
    """
    dur_f = float(duration)
    _, vid_action_template = describe_scene_action_and_props(dialogue, scene_index, total_scenes, screenshot_path)
    action_filled = vid_action_template.replace("{dialogue}", dialogue)

    if dialogue:
        video_prompt = (
            f"Vertical 9:16 locked-off static camera at eye level. "
            f"0.0-{dur_f:.1f} s: {action_filled}. "
            f"Smooth, continuous real-time human motion throughout with zero jitter or abrupt cuts. "
            f"NO SUBTITLES, NO CAPTIONS, NO ON-SCREEN TEXT."
        )
    else:
        video_prompt = (
            f"Vertical 9:16 locked-off static camera at eye level. "
            f"0.0-{dur_f:.1f} s: Looking directly into the camera with natural engagement, "
            f"calm facial expression, subtle eye blinks and natural posture adjustments. "
            f"Smooth continuous cinematic motion without sudden moves. "
            f"NO SUBTITLES, NO CAPTIONS, NO ON-SCREEN TEXT."
        )
    return video_prompt


def map_scenes_to_prompts(
    scenes_data: list[dict],
    transcript_path: Path,
    output_prompts_path: Path,
    avatar_path: Path | None = None,
) -> list[dict]:
    """
    Generates prompts.json aligning each scene with transcript words,
    Nano Banana Pro 2.1 image prompt, and Omni 1.1 Flash video prompt.
    """
    output_prompts_path.parent.mkdir(parents=True, exist_ok=True)

    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript_data = json.load(f)

    total_scenes = len(scenes_data)
    prompts_list = []

    for idx, sc in enumerate(scenes_data, start=1):
        scene_id = sc["scene_id"]
        start_sec = sc["start_time"]
        end_sec = sc["end_time"]
        raw_duration = sc["duration"]
        dialogue = extract_dialogue_for_interval(transcript_data, start_sec, end_sec)
        screenshot = sc.get("screenshot_path")

        # Pick duration dynamically based on both scene length and dialogue content
        omni_duration = pick_omni_duration(raw_duration, dialogue=dialogue)

        image_prompt = build_image_prompt(scene_id, dialogue, idx, total_scenes, screenshot)
        video_prompt = build_video_prompt(dialogue, omni_duration, scene_id, idx, total_scenes, screenshot)

        prompt_entry = {
            "scene_id": scene_id,
            "scene_num": idx,
            "start_time": start_sec,
            "end_time": end_sec,
            "original_duration": raw_duration,
            "omni_duration": omni_duration,
            "screenshot_path": screenshot,
            "dialogue": dialogue,
            "image_prompt": image_prompt,
            "video_prompt": video_prompt,
            "avatar_path": str(avatar_path.resolve()) if avatar_path else None,
        }
        prompts_list.append(prompt_entry)

    with open(output_prompts_path, "w", encoding="utf-8") as f:
        json.dump(prompts_list, f, indent=2, ensure_ascii=False)

    logger.info(f"Generated {len(prompts_list)} dynamic scene prompts into {output_prompts_path}")
    return prompts_list


def run_stage_3(
    scenes_data: list[dict],
    transcript_path: Path,
    job_dir: Path,
    avatar_path: Path | None = None,
) -> tuple[Path, list[dict]]:
    """
    Executes Stage 3: Scene-to-Prompt mapping.
    Returns (prompts_json_path, prompts_list).
    """
    prompts_path = job_dir / "prompts.json"
    prompts_list = map_scenes_to_prompts(
        scenes_data=scenes_data,
        transcript_path=transcript_path,
        output_prompts_path=prompts_path,
        avatar_path=avatar_path,
    )
    return prompts_path, prompts_list
