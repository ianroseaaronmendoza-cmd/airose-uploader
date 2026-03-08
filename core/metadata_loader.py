import os
import json
import re
from typing import List
from copy import deepcopy
from core.models import VideoAsset


# 🔧 UPDATE THESE PATHS
def _resolve_folder(env_name: str, fallback: str) -> str:
    candidate = (os.environ.get(env_name) or "").strip()
    if not candidate:
        return fallback
    return os.path.abspath(os.path.expanduser(candidate))


DEFAULT_METADATA_FOLDER = r"J:\My Drive\Automation\metadata"
DEFAULT_VIDEO_FOLDER = r"J:\My Drive\Automation\final_output"
METADATA_FOLDER = _resolve_folder("UPLOADER_METADATA_FOLDER", DEFAULT_METADATA_FOLDER)
VIDEO_FOLDER = _resolve_folder("UPLOADER_VIDEO_FOLDER", DEFAULT_VIDEO_FOLDER)


UPLOAD_STATUS_TEMPLATE = {
    "youtube": {
        "approved": False,
        "uploaded": False,
        "uploaded_at": None,
        "video_id": None,
        "error": None
    },
    "tiktok": {
        "approved": False,
        "uploaded": False,
        "uploaded_at": None,
        "video_id": None,
        "error": None
    },
    "instagram_facebook": {
        "approved": False,
        "uploaded": False,
        "uploaded_at": None,
        "video_id": None,
        "facebook_video_id": None,
        "instagram_media_id": None,
        "error": None
    }
}


# -----------------------------
# SCRIPT FORMATTERS
# -----------------------------

def strip_pause_tags(script: str) -> str:
    return re.sub(r"\[PAUSE.*?\]", "", script, flags=re.IGNORECASE)


_MINOR_TITLE_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "in",
    "nor", "of", "on", "or", "per", "the", "to", "vs", "via",
}

_HASHTAG_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "if", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the",
    "their", "this", "to", "was", "we", "with", "you", "your",
    "about", "after", "before", "can", "could", "did", "does", "had", "has",
    "have", "just", "make", "more", "over", "than", "then", "they", "them",
    "what", "when", "where", "which", "who", "why", "will",
    "guide", "video", "videos", "short", "shorts", "reel", "reels", "post", "posts",
}


def _lowercase_leading_word_if_continuation(line: str) -> str:
    """Lowercase a carried-over line start when it is not a new sentence."""
    match = re.match(r'^([\"\'(\[{]*)([A-Za-z]+)(.*)$', line)
    if not match:
        return line

    prefix, first_word, suffix = match.groups()
    if first_word == "I":
        return line
    if first_word.isupper() and len(first_word) > 1:
        return line

    return f"{prefix}{first_word[0].lower()}{first_word[1:]}{suffix}"


def _extract_urls(text: str) -> tuple[str, dict[str, str]]:
    pattern = re.compile(r"(https?://\S+|www\.\S+)")
    replacements: dict[str, str] = {}

    def _replace(match: re.Match[str]) -> str:
        key = f"__url_{len(replacements)}__"
        replacements[key] = match.group(0)
        return key

    return pattern.sub(_replace, text), replacements


def _restore_urls(text: str, replacements: dict[str, str]) -> str:
    restored = text
    for key, value in replacements.items():
        restored = restored.replace(key, value)
    return restored


def _tokenize_hashtag_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9]+", text.lower())


def _is_meaningful_hashtag_word(word: str) -> bool:
    return (
        len(word) >= 3
        and not word.isdigit()
        and word not in _HASHTAG_STOP_WORDS
    )


def _format_hashtag_phrase(words: tuple[str, ...]) -> str:
    if not words or not all(_is_meaningful_hashtag_word(word) for word in words):
        return ""
    body = "".join(word.capitalize() for word in words)
    if not body or body[0].isdigit() or len(body) > 40:
        return ""
    return f"#{body}"


def _iter_hashtag_phrase_candidates(text: str) -> list[tuple[str, tuple[str, ...]]]:
    tokens = _tokenize_hashtag_words(text)
    candidates: list[tuple[str, tuple[str, ...]]] = []

    for size in (3, 2):
        if len(tokens) < size:
            continue
        for idx in range(len(tokens) - size + 1):
            phrase = tuple(tokens[idx:idx + size])
            tag = _format_hashtag_phrase(phrase)
            if tag:
                candidates.append((tag, phrase))

    return candidates


def _iter_hashtag_single_candidates(text: str) -> list[tuple[str, tuple[str, ...]]]:
    tokens = _tokenize_hashtag_words(text)
    candidates: list[tuple[str, tuple[str, ...]]] = []

    for token in tokens:
        tag = _format_hashtag_phrase((token,))
        if tag:
            candidates.append((tag, (token,)))

    return candidates


def _is_subphrase(phrase: tuple[str, ...], full_phrase: tuple[str, ...]) -> bool:
    if len(phrase) >= len(full_phrase):
        return False
    for idx in range(len(full_phrase) - len(phrase) + 1):
        if full_phrase[idx:idx + len(phrase)] == phrase:
            return True
    return False


def style_title_text(title: str) -> str:
    text = re.sub(r"\s+", " ", title).strip()
    if not text:
        return ""

    parts = text.split(" ")
    formatted = []

    for i, word in enumerate(parts):
        lower_word = word.lower()
        is_first = i == 0
        is_last = i == len(parts) - 1

        if word.isupper() and len(word) > 1:
            formatted.append(word)
        elif not is_first and not is_last and lower_word in _MINOR_TITLE_WORDS:
            formatted.append(lower_word)
        else:
            formatted.append(lower_word.capitalize())

    return " ".join(formatted)


def style_description_text(description: str) -> str:
    raw_lines = [re.sub(r"\s+", " ", line.strip()) for line in description.splitlines()]
    lines = []
    for line in raw_lines:
        if not line:
            continue
        if lines and not re.search(r"[.!?][\"')\]]*$", lines[-1]):
            line = _lowercase_leading_word_if_continuation(line)
        lines.append(line)

    text = " ".join(lines)
    if not text:
        return ""

    # Protect URLs before punctuation/letter spacing and lowercasing.
    text_with_placeholders, url_map = _extract_urls(text)

    # Ensure spacing after punctuation where a word immediately follows.
    text_with_placeholders = re.sub(
        r"([.!?,;:])([A-Za-z])",
        r"\1 \2",
        text_with_placeholders,
    )
    text_with_placeholders = re.sub(r"\s+", " ", text_with_placeholders).strip()

    # Normalize sentence case.
    lowered = text_with_placeholders.lower()

    # Capitalize sentence starts.
    lowered = re.sub(
        r"(^|[.!?]\s+)([a-z])",
        lambda m: f"{m.group(1)}{m.group(2).upper()}",
        lowered,
    )

    # Capitalize standalone "i" pronoun.
    lowered = re.sub(r"\bi\b", "I", lowered)
    return _restore_urls(lowered, url_map).strip()


def generate_title_from_script(script: str) -> str:
    cleaned = strip_pause_tags(script)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return ""

    first_line = lines[0]
    return style_title_text(first_line)


def generate_description_from_script(script: str) -> str:
    cleaned = strip_pause_tags(script)
    lines = [line.rstrip() for line in cleaned.splitlines()]
    return style_description_text("\n".join(lines).strip())


def build_youtube_title_with_hashtags(
    title: str,
    description: str,
    max_tags: int = 3,
    max_length: int = 100,
) -> str:
    base_title = style_title_text(title)
    if not base_title:
        return ""

    source_text = f"{base_title} {description}"
    existing = {tag.lower() for tag in re.findall(r"#([A-Za-z0-9_]+)", source_text)}
    phrase_candidates = (
        _iter_hashtag_phrase_candidates(base_title)
        + _iter_hashtag_phrase_candidates(description)
    )
    single_candidates = (
        _iter_hashtag_single_candidates(base_title)
        + _iter_hashtag_single_candidates(description)
    )

    tags: list[str] = []
    seen_tags: set[str] = set()
    selected_phrases: list[tuple[str, ...]] = []
    for candidate_pool in (phrase_candidates, single_candidates):
        for candidate, phrase in candidate_pool:
            key = candidate[1:].lower()
            if key in seen_tags or key in existing:
                continue

            # Avoid redundant one-word tags when a longer selected phrase already covers it.
            if len(phrase) == 1 and any(phrase[0] in selected for selected in selected_phrases if len(selected) > 1):
                continue
            if any(_is_subphrase(phrase, selected) for selected in selected_phrases if len(selected) > len(phrase)):
                continue

            trial_suffix = " " + " ".join(tags + [candidate])
            if len(base_title) + len(trial_suffix) > max_length:
                continue

            seen_tags.add(key)
            selected_phrases.append(phrase)
            tags.append(candidate)
            if len(tags) >= max_tags:
                break
        if len(tags) >= max_tags:
            break

    return base_title if not tags else f"{base_title} {' '.join(tags)}"


# -----------------------------
# NORMALIZATION
# -----------------------------

def normalize_metadata(data: dict, file_path: str) -> dict:
    original_data = deepcopy(data)
    file_id = os.path.basename(file_path).replace(".json", "")

    # Ensure ID
    if "id" not in data or not data["id"]:
        data["id"] = file_id

    # Ensure base fields
    data.setdefault("video", f"{data['id']}.mp4")
    data.setdefault("video_path", "")
    data.setdefault("production_mode", "")
    data.setdefault("preset", "")
    data.setdefault("script", "")
    data.setdefault("duration_seconds", 0.0)
    data.setdefault("watermark_text", "")
    data.setdefault("watermark_image", "")
    data.setdefault("created_at", "")

    # Auto-generate title/description if empty
    script = data.get("script", "")

    if not data.get("title"):
        data["title"] = generate_title_from_script(script)
    else:
        data["title"] = style_title_text(data["title"])

    if not data.get("description"):
        data["description"] = generate_description_from_script(script)
    else:
        data["description"] = style_description_text(data["description"])

    # Ensure upload_status
    if "upload_status" not in data or not isinstance(data["upload_status"], dict):
        data["upload_status"] = deepcopy(UPLOAD_STATUS_TEMPLATE)
    else:
        for platform, template in UPLOAD_STATUS_TEMPLATE.items():
            if platform not in data["upload_status"]:
                data["upload_status"][platform] = deepcopy(template)
            else:
                for key, value in template.items():
                    data["upload_status"][platform].setdefault(key, value)

    # Write back if modified
    if data != original_data:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    return data


# -----------------------------
# LOADER
# -----------------------------

def load_metadata() -> List[VideoAsset]:
    assets = []

    if not os.path.exists(METADATA_FOLDER):
        return assets

    for file in os.listdir(METADATA_FOLDER):
        if not file.endswith(".json"):
            continue

        full_path = os.path.join(METADATA_FOLDER, file)

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)

            data = normalize_metadata(raw_data, full_path)

            asset_id = data["id"]
            video_path = os.path.join(VIDEO_FOLDER, f"{asset_id}.mp4")
            video_exists = os.path.exists(video_path)

            asset = VideoAsset(
                id=asset_id,
                metadata_path=full_path,
                video_path=video_path,
                video_exists=video_exists,
                production_mode=data.get("production_mode", ""),
                duration=data.get("duration_seconds", 0.0),
                created_at=data.get("created_at", ""),
                script=data.get("script", ""),
                upload_status=data.get("upload_status", {}),
            )

        except Exception as e:
            asset = VideoAsset(
                id=file.replace(".json", ""),
                metadata_path=full_path,
                video_path="",
                video_exists=False,
                production_mode="",
                duration=0.0,
                created_at="",
                script="",
                upload_status={},
                error_state=True,
                error_message=str(e),
            )

        assets.append(asset)

    return assets
