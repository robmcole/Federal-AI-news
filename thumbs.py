"""Fetch and cache story preview images for the static build.

Story pages are read at build time for an og:image or twitter:image. The image
is downloaded, resized, and stored under .cache so later builds can reuse it.
Failures become fallback tiles; remote image URLs are never written into pages.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / ".cache"
CACHE_INDEX = CACHE_DIR / "thumbs.json"
CACHE_FILES = CACHE_DIR / "files"

OK_TTL = 14 * 24 * 60 * 60
FAIL_TTL = 3 * 24 * 60 * 60
HTML_LIMIT = 80_000
IMAGE_LIMIT = 5_000_000
FETCH_TIMEOUT = 8
MAX_WIDTH = 720
WORKERS = 8

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

STORY_LINE = re.compile(r"^\*\*\[.*?\]\((https?://[^)\s]+)\)\*\*\s*$")
IMAGE_LINE = re.compile(r"^\*(?:Image|Thumbnail):\s*(https?://\S+?)\*\s*$")
HEADING_LINE = re.compile(r"^##\s+")
META_TAG = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
ATTR = re.compile(r"([:\w-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.IGNORECASE)
IMAGE_KEYS = ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src")

SKIP_HOST_SUFFIXES = ("sam.gov",)


def story_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def collect_story_urls(body: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for line in body.splitlines():
        match = STORY_LINE.match(line.strip())
        if match and match.group(1) not in seen:
            seen.add(match.group(1))
            urls.append(match.group(1))
    return urls


def normalize_story_blocks(body: str) -> str:
    """Keep each story's headline, summary, and source lines in one paragraph.

    Blank lines are preserved between stories. Blank lines inside a story are
    removed so Markdown does not split the card into separate paragraphs.
    """
    lines = body.splitlines()
    result: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not STORY_LINE.match(stripped):
            result.append(lines[index])
            index += 1
            continue
        if result and result[-1].strip():
            result.append("")
        result.append(lines[index])
        index += 1
        while index < len(lines):
            nxt = lines[index].strip()
            if nxt == "":
                look = index + 1
                while look < len(lines) and not lines[look].strip():
                    look += 1
                if look >= len(lines):
                    break
                peek = lines[look].strip()
                if (
                    STORY_LINE.match(peek)
                    or peek.startswith("## ")
                    or peek.startswith("**TL;DR:**")
                    or peek.startswith("- ")
                ):
                    break
                index = look
                continue
            if STORY_LINE.match(nxt) or nxt.startswith("## "):
                break
            result.append(lines[index])
            index += 1
    return "\n".join(result)


def image_overrides(body: str) -> dict[str, str]:
    """Map a story URL to an optional ``*Image: https://...`` line beneath it."""
    found: dict[str, str] = {}
    current: str | None = None
    for line in body.splitlines():
        stripped = line.strip()
        story = STORY_LINE.match(stripped)
        if story:
            current = story.group(1)
            continue
        if HEADING_LINE.match(stripped):
            current = None
            continue
        image = IMAGE_LINE.match(stripped)
        if image and current:
            found[current] = image.group(1)
    return found


def _load_index() -> dict:
    if not CACHE_INDEX.is_file():
        return {"entries": {}}
    try:
        data = json.loads(CACHE_INDEX.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"entries": {}}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"entries": {}}
    return data


def _save_index(index: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_INDEX.write_text(json.dumps(index, indent=2), encoding="utf-8")


def _fresh(entry: dict, override: str | None, now: float) -> bool:
    if entry.get("override") != override:
        return False
    filename = entry.get("file")
    if entry.get("ok") and filename:
        path = CACHE_FILES / filename
        if not path.is_file() or path.stat().st_size == 0:
            return False
    age = now - float(entry.get("fetched") or 0)
    return age < (OK_TTL if entry.get("ok") else FAIL_TTL)


def _host_skipped(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == suffix or host.endswith("." + suffix) for suffix in SKIP_HOST_SUFFIXES)


def _fetch(url: str, limit: int) -> tuple[bytes, str, str]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
        final = response.geturl()
        content_type = response.headers.get_content_type()
        chunks: list[bytes] = []
        total = 0
        while total < limit:
            block = response.read(min(16384, limit - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
        return b"".join(chunks), final, content_type


def _meta_map(tag: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in ATTR.finditer(tag):
        key = match.group(1).lower()
        found[key] = match.group(2) if match.group(2) is not None else match.group(3) or ""
    return found


def og_image(html_bytes: bytes, base_url: str) -> str | None:
    text = html_bytes.decode("utf-8", "replace")
    ranked: dict[str, str] = {}
    for tag in META_TAG.findall(text):
        attrs = _meta_map(tag)
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        content = attrs.get("content", "").strip()
        if key in IMAGE_KEYS and content and key not in ranked:
            ranked[key] = content
    for key in IMAGE_KEYS:
        if key in ranked:
            return urljoin(base_url, ranked[key])
    return None


def _to_jpeg(data: bytes) -> bytes | None:
    try:
        with Image.open(BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            if image.mode in ("RGBA", "LA", "P"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                rgba = image.convert("RGBA")
                background.paste(rgba, mask=rgba.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")
            if image.width > MAX_WIDTH:
                height = max(1, round(image.height * (MAX_WIDTH / image.width)))
                image = image.resize((MAX_WIDTH, height), Image.Resampling.LANCZOS)
            output = BytesIO()
            image.save(output, format="JPEG", quality=74, optimize=True)
            return output.getvalue()
    except Exception:
        return None


def _download_image(image_url: str) -> bytes | None:
    if _host_skipped(image_url):
        return None
    try:
        data, _final, content_type = _fetch(image_url, IMAGE_LIMIT)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if content_type.startswith("image/svg"):
        return None
    if data.startswith(b"<svg") or data[:40].lower().startswith(b"<?xml"):
        return None
    return _to_jpeg(data)


def _preview_for(page_url: str, override: str | None) -> bytes | None:
    if override:
        return _download_image(override)
    if _host_skipped(page_url) or page_url.lower().split("?", 1)[0].endswith(".pdf"):
        return None
    try:
        data, final, content_type = _fetch(page_url, HTML_LIMIT)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if _host_skipped(final):
        return None
    if content_type.startswith("image/"):
        return _to_jpeg(data)
    image_url = og_image(data, final)
    if not image_url:
        return None
    return _download_image(image_url)


def _store(page_url: str, override: str | None, jpeg: bytes | None, now: float) -> dict:
    filename = f"{story_id(page_url)}.jpg"
    entry = {
        "ok": bool(jpeg),
        "file": filename if jpeg else "",
        "override": override,
        "fetched": now,
    }
    if jpeg:
        CACHE_FILES.mkdir(parents=True, exist_ok=True)
        (CACHE_FILES / filename).write_bytes(jpeg)
    return entry


def resolve_thumbnails(pairs: list[tuple[str, str | None]]) -> dict[str, str | None]:
    """Return story URL -> ``{id}.jpg`` when a preview image was saved."""
    socket.setdefaulttimeout(FETCH_TIMEOUT)
    unique: dict[str, str | None] = {}
    for url, override in pairs:
        if url not in unique or (override and not unique[url]):
            unique[url] = override

    index = _load_index()
    entries: dict = index["entries"]
    now = time.time()
    result: dict[str, str | None] = {}
    pending: list[tuple[str, str | None]] = []

    for url, override in unique.items():
        entry = entries.get(url)
        if isinstance(entry, dict) and _fresh(entry, override, now):
            result[url] = entry["file"] if entry.get("ok") and entry.get("file") else None
        else:
            pending.append((url, override))

    saved = 0
    failed = 0
    if pending:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(_preview_for, url, override): (url, override)
                for url, override in pending
            }
            for future in as_completed(futures):
                url, override = futures[future]
                try:
                    jpeg = future.result()
                except Exception:
                    jpeg = None
                entry = _store(url, override, jpeg, now)
                entries[url] = entry
                result[url] = entry["file"] if entry["ok"] else None
                if entry["ok"]:
                    saved += 1
                else:
                    failed += 1
                    print(f"  no preview: {url}")

    if pending:
        _save_index(index)
    cached_ok = sum(1 for url in unique if url not in {item[0] for item in pending} and result.get(url))
    cached_fail = len(unique) - len(pending) - cached_ok
    print(
        f"Thumbnails: {saved} fetched, {cached_ok} cached, "
        f"{failed + cached_fail} fallback ({len(unique)} stories)"
    )
    return result


def cached_jpeg(filename: str) -> Path | None:
    path = CACHE_FILES / filename
    if filename and path.is_file():
        return path
    return None
