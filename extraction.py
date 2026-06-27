"""
extraction.py — sends EB roster screenshots to Claude's vision API and
returns structured (rank, name, successful_actions) tuples.
"""
import base64
import json
import os
import re
import anthropic

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

EXTRACTION_PROMPT = """You are looking at a screenshot from a mobile game called Kingdoms at War. \
It shows a leaderboard titled "Top clan players in this Epic Battle". Each entry has a rank number, \
a player name, a "Plunder earned" gold amount, and a "Successful actions" count, plus some item icons.

Extract EVERY entry visible in this image. For each one, return:
- rank (the number before the name, e.g. "19." -> 19)
- name (the player name exactly as written, preserving all special characters, dashes, underscores,
  number/letter substitutions like 0 for O or 1 for I — do not "correct" or normalize the spelling)
- successful_actions (the integer after "Successful actions:")

Ignore plunder/gold amounts and item icons entirely — we don't need them.

Return ONLY a JSON array, no other text, no markdown code fences. Format:
[{"rank": 19, "name": "Slycvmmings", "successful_actions": 60}, ...]

If a name or number is genuinely unreadable/cut off, use null for that field rather than guessing.
"""


def _load_image_b64(path: str):
    with open(path, "rb") as f:
        data = f.read()
    media_type = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    return media_type, base64.standard_b64encode(data).decode("utf-8")


def extract_from_screenshot(image_path: str):
    """
    Sends a single screenshot to Claude vision and returns a list of dicts:
    [{"rank": int, "name": str, "successful_actions": int}, ...]
    Raises ValueError if the response can't be parsed as JSON.
    """
    media_type, b64 = _load_image_b64(image_path)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {"type": "text", "text": EXTRACTION_PROMPT},
                ],
            }
        ],
    )

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    # Strip stray markdown fences just in case the model adds them
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Could not parse extraction response as JSON: {e}\nRaw response: {text[:500]}")

    if not isinstance(parsed, list):
        raise ValueError(f"Expected a JSON array, got: {type(parsed)}")

    return parsed


def extract_from_batch(image_paths: list[str]):
    """
    Process multiple screenshots (one EB spread across several scrolling shots).
    Returns a merged, de-duplicated list across all images.
    De-dup key: (rank) — if the same rank appears twice (overlapping scroll shots),
    keep the first occurrence and skip the rest.
    """
    seen_ranks = set()
    merged = []
    errors = []

    for path in image_paths:
        try:
            entries = extract_from_screenshot(path)
        except ValueError as e:
            errors.append(f"{os.path.basename(path)}: {e}")
            continue

        for entry in entries:
            rank = entry.get("rank")
            if rank is not None and rank in seen_ranks:
                continue
            if rank is not None:
                seen_ranks.add(rank)
            merged.append(entry)

    return merged, errors
