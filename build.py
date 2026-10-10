#!/usr/bin/env python3
"""Build the Federal AI News site from issues/*.md.

Adding a digest is one new file: issues/YYYY-MM-DD.md. This script discovers
every issue, so the home page, archive, permalinks, and feed need no hand edits.
Story preview images are fetched into a local cache at build time.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import markdown

from thumbs import (
    cached_jpeg,
    collect_story_urls,
    image_overrides,
    normalize_story_blocks,
    resolve_thumbnails,
    story_id,
)

ROOT = Path(__file__).resolve().parent
ISSUES_DIR = ROOT / "issues"
DIST = ROOT / "dist"
ASSETS = ROOT / "assets"

SITE_NAME = "Federal AI News"
TAGLINE = "A weekday TLDR of AI and tech across the U.S. federal government"
SITE_URL = os.environ.get(
    "SITE_URL", "https://robmcole.github.io/Federal-AI-news"
).rstrip("/")
MEDIA_TOKEN = "__FAN_MEDIA__/"

FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
TLDR_RE = re.compile(r"^\*\*TL;DR:\*\*\s*(.+)$", re.MULTILINE)
ITEM_RE = re.compile(
    r'<p><strong>(<a href="[^"]+">.*?</a>)</strong>\s*(.*?)</p>',
    re.DOTALL,
)
READ_TIME_RE = re.compile(
    r"^\(\s*(\d+)\s*(?:-|\s)?(?:minutes?|mins?)\.?\s+read\s*\)\s*",
    re.IGNORECASE,
)
EXTERNAL_LINK_RE = re.compile(r'<a href="([^"]+)"([^>]*)>')
SCORE_RE = re.compile(r"\s*·?\s*Reliability Score:\s*([1-5])/5")
H2_RE = re.compile(r"<h2>(.*?)</h2>", re.DOTALL)

MD = markdown.Markdown(extensions=["extra", "sane_lists"])

BUCKET_RULES = (
    ("deadlines", ("deadline",)),
    ("events", ("event",)),
    ("safety", ("safety", "incident")),
    ("defense", ("defense", "national security")),
    ("acquisition", ("acquisition", "contract")),
    ("congress", ("congress", "legislation")),
    ("civilian", ("civilian",)),
    ("industry", ("industry", "vendor")),
    ("executive", ("white house", "executive")),
)

BUCKET_ICONS = {
    "executive": '<path d="M4 20h16v-1.5H4V20zm1.5-3h13V9.2L12 4.5 5.5 9.2V17zm3.2-1.6v-2.6h2.1v2.6H8.7zm4.5 0v-2.6h2.1v2.6h-2.1z"/>',
    "congress": '<path d="M3 20h2.2V9H3v11zm7.9 0h2.2V9h-2.2v11zM18.8 20H21V9h-2.2v11zM2.5 8.2 12 3l9.5 5.2V9H2.5V8.2z"/>',
    "acquisition": '<path d="M7 3.5h7.2L18 7.3V20.5H7V3.5zm6.5 1.2V8H16l-2.5-3.3zM9 11h6v1.4H9V11zm0 3h6v1.4H9V14z"/>',
    "defense": '<path d="M12 3.2 5 6v5.3c0 4 2.9 7.2 7 8.5 4.1-1.3 7-4.5 7-8.5V6l-7-2.8z"/>',
    "civilian": '<path d="M4 20V9.5L12 4l8 5.5V20h-5.2v-5.2H9.2V20H4z"/>',
    "safety": '<path d="M8 10V7.8a4 4 0 0 1 8 0V10h1.2v9.2H6.8V10H8zm1.6 0h4.8V7.8a2.4 2.4 0 0 0-4.8 0V10z"/>',
    "industry": '<path d="M4 20V9.2l5.2 2.6V9.2L14.5 12V8l5.5 2.8V20H4z"/>',
    "deadlines": '<path d="M12 4.2a7.8 7.8 0 1 0 0 15.6 7.8 7.8 0 0 0 0-15.6zm-.8 3.6h1.6v4.4l3.2 1.9-.8 1.3-4-2.4V7.8z"/>',
    "events": '<path d="M7 5.2V3.8h1.6v1.4h6.8V3.8H17v1.4h2.2V20H4.8V5.2H7zm11.6 5.2H7.4V18h11.2v-7.6z"/>',
    "general": '<path d="M12 4.5 13.6 10H19l-4.4 3.3 1.7 5.2L12 15.2 7.7 18.5l1.7-5.2L5 10h5.4L12 4.5z"/>',
}


@dataclass
class Issue:
    slug: str
    date: datetime
    title: str
    html: str
    tldr: str
    stories: list[dict] = field(default_factory=list)

    @property
    def date_iso(self) -> str:
        return self.date.strftime("%Y-%m-%d")

    @property
    def date_long(self) -> str:
        return f"{self.date.strftime('%A, %B')} {self.date.day}, {self.date.year}"

    @property
    def date_short(self) -> str:
        return f"{self.date.strftime('%b')} {self.date.day}, {self.date.year}"

    @property
    def path(self) -> str:
        return f"issues/{self.slug}/"


def rel_url(from_dir: str, target: str) -> str:
    """URL of a site-root path relative to a page directory.

    from_dir is '' for the site root, or a directory without a trailing slash.
    A target that ends in '/' (or is '') is treated as a directory link.
    """
    start = from_dir if from_dir else "."
    as_directory = target.endswith("/") or target == ""
    dest = target[:-1] if target.endswith("/") else target
    if dest == "":
        dest = "."
        as_directory = True
    relative = os.path.relpath(dest, start=start).replace(os.sep, "/")
    if as_directory:
        if relative in (".", ""):
            return "./"
        return relative + "/"
    return relative


def media_prefix(from_dir: str) -> str:
    if not from_dir:
        return ""
    return "../" * (from_dir.count("/") + 1)


def apply_media(page_html: str, prefix: str) -> str:
    return page_html.replace(MEDIA_TOKEN, prefix)


def _check_relative_urls() -> None:
    cases = {
        ("", "assets/style.css"): "assets/style.css",
        ("", "archive/"): "archive/",
        ("", "favorites/"): "favorites/",
        ("", "issues/2026-10-02/"): "issues/2026-10-02/",
        ("", "feed.xml"): "feed.xml",
        ("", ""): "./",
        ("archive", ""): "../",
        ("archive", "assets/style.css"): "../assets/style.css",
        ("favorites", "media/thumbs/abc.jpg"): "../media/thumbs/abc.jpg",
        ("issues/2026-10-02", ""): "../../",
        ("issues/2026-10-02", "archive/"): "../../archive/",
        ("issues/2026-10-02", "favorites/"): "../../favorites/",
        ("issues/2026-10-02", "assets/favorites.js"): "../../assets/favorites.js",
        ("issues/2026-10-02", "media/thumbs/abc.jpg"): "../../media/thumbs/abc.jpg",
    }
    for (from_dir, target), expected in cases.items():
        got = rel_url(from_dir, target)
        if got != expected:
            raise SystemExit(
                f"rel_url({from_dir!r}, {target!r}) = {got!r}, expected {expected!r}"
            )
    if media_prefix("issues/2026-10-02") != "../../":
        raise SystemExit("media prefix for an issue page should be ../../")
    if media_prefix("") != "":
        raise SystemExit("media prefix at the site root should be empty")


def parse_front_matter(text: str, filename: str) -> tuple[dict[str, str], str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.startswith("\ufeff"):
        normalized = normalized.lstrip("\ufeff")
    match = FRONT_MATTER_RE.match(normalized)
    if not match:
        raise SystemExit(f"{filename}: missing YAML front matter (--- date/title ---)")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise SystemExit(f"{filename}: bad front matter line: {line}")
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].replace('\\"', '"').replace("\\'", "'")
        meta[key.strip()] = value
    for required in ("date", "title"):
        if not meta.get(required):
            raise SystemExit(f"{filename}: front matter is missing {required}")
    return meta, match.group(2).lstrip("\n")


def score_badge(score: str) -> str:
    return (
        f'<span class="score score-{score}">'
        f'<span class="score-k">Reliability Score:</span> {score}/5</span>'
    )


def initials(source: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", source or "")
    if not words:
        return "AI"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def plain_text(fragment: str) -> str:
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def bucket_key(title: str) -> str:
    lowered = html.unescape(re.sub(r"<[^>]+>", "", title)).lower()
    for key, needles in BUCKET_RULES:
        if any(needle in lowered for needle in needles):
            return key
    return "general"


def star_svg() -> str:
    return (
        '<svg viewBox="0 0 24 24" aria-hidden="true" width="18" height="18">'
        '<path d="M12 3.2l2.55 5.17 5.7.83-4.12 4.02.97 5.68L12 16.2'
        'l-5.1 2.7.97-5.68L3.75 9.2l5.7-.83L12 3.2z"/></svg>'
    )


def bucket_icon(key: str) -> str:
    path = BUCKET_ICONS.get(key, BUCKET_ICONS["general"])
    return (
        f'<svg viewBox="0 0 24 24" aria-hidden="true" width="16" height="16">{path}</svg>'
    )


def fav_button(headline: str) -> str:
    label = html.escape(f"Save story: {headline}", quote=True)
    return (
        f'<button type="button" class="fav-btn" aria-pressed="false" '
        f'aria-label="{label}">{star_svg()}</button>'
    )


def take_read_time(text: str) -> tuple[str, str]:
    """Pull a leading `(N minute read)` off a story body.

    Returns the display label (`N min read`) and the body with that prefix gone.
    """
    match = READ_TIME_RE.match(text)
    if not match:
        return "", text
    return f"{int(match.group(1))} min read", text[match.end() :].strip()


def enhance(
    article_html: str,
    *,
    thumbs: dict[str, str | None],
    issue_date: str,
    issue_label: str,
) -> tuple[str, list[dict]]:
    """Turn digest conventions into cards, callouts, and score badges.

    Items in the source files are often one Markdown paragraph (title, summary,
    and the italic source line, with no blank lines). Both that shape and a
    blank-line-separated shape are handled.
    """
    stories: list[dict] = []
    article_html = SCORE_RE.sub(lambda m: " " + score_badge(m.group(1)), article_html)
    article_html = re.sub(r"<em>(Source:)", r'<em class="item-meta">\1', article_html)
    article_html = re.sub(
        r"<em>(Primary document:)",
        r'<em class="item-meta item-primary">\1',
        article_html,
    )
    article_html = re.sub(
        r"<em>(Image:)",
        r'<em class="item-meta item-image">\1',
        article_html,
    )
    article_html = re.sub(
        r"<em>(Thumbnail:)",
        r'<em class="item-meta item-image">\1',
        article_html,
    )

    def split_item(match: re.Match[str]) -> str:
        title_link = match.group(1)
        rest = match.group(2)
        href_match = re.search(r'href="([^"]+)"', title_link)
        if not href_match:
            return match.group(0)
        url = html.unescape(href_match.group(1))
        headline = plain_text(title_link)
        metas = re.findall(
            r'<em class="item-meta[^"]*">.*?</em>', rest, flags=re.DOTALL
        )
        body = rest
        for meta in metas:
            body = body.replace(meta, "", 1)
        body = body.strip()
        read_time, body = take_read_time(body)
        summary = plain_text(body)
        source = ""
        score = ""
        primary = ""
        source_html = ""
        for meta in metas:
            meta_match = re.match(r'<em class="([^"]+)">(.*)</em>', meta, re.DOTALL)
            if not meta_match:
                continue
            classes, inner = meta_match.group(1), meta_match.group(2).strip()
            if "item-image" in classes:
                continue
            if "item-primary" in classes:
                primary = f'<p class="{classes}">{inner}</p>'
                continue
            source_match = re.search(r"Source:\s*(.+)", plain_text(inner))
            if source_match:
                source = re.sub(r"\s*Reliability Score:.*", "", source_match.group(1)).strip(" ·")
            score_match = re.search(r"score-([1-5])", inner)
            if score_match:
                score = score_match.group(1)
            source_html = f'<p class="{classes}">{inner}</p>'
        filename = thumbs.get(url) or ""
        thumb_path = f"media/thumbs/{filename}" if filename else ""
        payload = {
            "id": story_id(url),
            "headline": headline,
            "url": url,
            "summary": summary,
            "source": source,
            "score": score,
            "issueDate": issue_date,
            "issueLabel": issue_label,
            "thumbnail": thumb_path,
            "readTime": read_time,
        }
        stories.append(payload)
        loading = "lazy"
        href = html.escape(url, quote=True)
        open_label = html.escape(f"Open story: {headline}", quote=True)
        if filename:
            media = (
                f'<a href="{href}" class="story-media" aria-label="{open_label}">'
                f'<img src="{MEDIA_TOKEN}{thumb_path}" alt="" width="720" height="450" '
                f'loading="{loading}" decoding="async"></a>'
            )
        else:
            media = (
                f'<a href="{href}" class="story-media is-fallback" aria-label="{open_label}">'
                f'<span class="fallback-initials">{html.escape(initials(source))}</span>'
                f'<span class="fallback-outlet">{html.escape(source or "Source")}</span>'
                f"</a>"
            )
        summary_html = f'<p class="story-summary">{body}</p>' if body else ""
        read_html = (
            f'<p class="read-time">{html.escape(read_time)}</p>' if read_time else ""
        )
        data = html.escape(json.dumps(payload, ensure_ascii=False), quote=True)
        return (
            f'<article class="story" data-story-id="{payload["id"]}" data-story="{data}">\n'
            f"{media}\n"
            f'<div class="story-body">\n'
            f'<h3 class="item-title">{title_link}</h3>\n'
            f"{read_html}\n"
            f"{summary_html}\n"
            f'<div class="story-foot">\n'
            f"{source_html if source or score else ''}\n"
            f"{fav_button(headline)}\n"
            f"</div>\n"
            f"{primary}\n"
            f"</div>\n"
            f"</article>"
        )

    article_html = ITEM_RE.sub(split_item, article_html)
    article_html = re.sub(
        r'<p><em class="(item-meta[^"]*)">(.*?)</em></p>',
        r'<p class="\1">\2</p>',
        article_html,
        flags=re.DOTALL,
    )
    article_html = re.sub(
        r"\s*<p class=\"item-meta item-image\">.*?</p>",
        "",
        article_html,
        flags=re.DOTALL,
    )
    article_html = re.sub(
        r"<p><strong>TL;DR:</strong>\s*(.*?)</p>",
        r'<aside class="tldr"><p><span class="tldr-label">TL;DR</span> \1</p></aside>',
        article_html,
        count=1,
        flags=re.DOTALL,
    )
    article_html = re.sub(
        r"<p><em>(Reliability Score:.*?)</em></p>",
        r'<p class="score-legend">\1</p>',
        article_html,
        count=1,
        flags=re.DOTALL,
    )
    article_html = tag_buckets(article_html)
    if stories:
        article_html = article_html.replace('loading="lazy"', 'loading="eager"', 1)
    return externalize(article_html), stories


def tag_buckets(article_html: str) -> str:
    parts = H2_RE.split(article_html)
    # split keeps captured groups: text, h2 inner, text, h2 inner...
    if len(parts) == 1:
        return article_html
    rebuilt = [parts[0]]
    current = "general"
    for index in range(1, len(parts), 2):
        inner = parts[index]
        current = bucket_key(inner)
        rebuilt.append(
            f'<h2 class="bucket bucket-{current}">'
            f'<span class="bucket-icon" aria-hidden="true">{bucket_icon(current)}</span>'
            f'<span class="bucket-label">{inner}</span></h2>'
        )
        following = parts[index + 1] if index + 1 < len(parts) else ""
        following = following.replace(
            '<article class="story"',
            f'<article class="story story-{current}"',
            # only the class attribute at the start; data-story-id follows
        )
        rebuilt.append(following)
    return "".join(rebuilt)


def externalize(article_html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        href, attrs = match.group(1), match.group(2)
        if href.startswith(("http://", "https://", "//")):
            if "target=" not in attrs:
                attrs += ' target="_blank"'
            if "rel=" not in attrs:
                attrs += ' rel="noopener noreferrer"'
        return f'<a href="{href}"{attrs}>'

    return EXTERNAL_LINK_RE.sub(repl, article_html)


def assert_external_links(article_html: str, slug: str) -> None:
    for match in EXTERNAL_LINK_RE.finditer(article_html):
        href, attrs = match.group(1), match.group(2)
        if not href.startswith(("http://", "https://", "//")):
            continue
        if 'target="_blank"' not in attrs:
            raise SystemExit(f"{slug}: external link missing target=_blank: {href}")
        if "noopener" not in attrs:
            raise SystemExit(f"{slug}: external link missing rel=noopener: {href}")


def assert_cards(article_html: str, slug: str) -> None:
    cards = len(re.findall(r'<article class="story', article_html))
    buttons = len(re.findall(r'class="fav-btn"', article_html))
    if cards != buttons:
        raise SystemExit(f"{slug}: {cards} stories but {buttons} favorite buttons")
    if cards and article_html.count('aria-pressed="false"') < cards:
        raise SystemExit(f"{slug}: a favorite button is missing aria-pressed")


def extract_tldr(body: str) -> str:
    match = TLDR_RE.search(body)
    if not match:
        return ""
    text = match.group(1).strip()
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"[*_`]", "", text)


def load_issues() -> list[Issue]:
    if not ISSUES_DIR.is_dir():
        return []
    pending: list[tuple[str, datetime, str, str]] = []
    seen: set[str] = set()
    pairs: list[tuple[str, str | None]] = []
    for path in sorted(ISSUES_DIR.glob("*.md")):
        match = FILENAME_RE.match(path.name)
        if not match:
            raise SystemExit(f"{path.name}: issue files must be named YYYY-MM-DD.md")
        slug = match.group(1)
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"), path.name)
        try:
            date = datetime.strptime(meta["date"], "%Y-%m-%d")
        except ValueError as exc:
            raise SystemExit(f"{path.name}: date must be YYYY-MM-DD") from exc
        if meta["date"] != slug:
            raise SystemExit(
                f"{path.name}: front matter date {meta['date']} does not match the filename"
            )
        if slug in seen:
            raise SystemExit(f"duplicate issue date {slug}")
        seen.add(slug)
        body = normalize_story_blocks(body)
        overrides = image_overrides(body)
        for url in collect_story_urls(body):
            pairs.append((url, overrides.get(url)))
        pending.append((slug, date, meta["title"], body))

    thumbs = resolve_thumbnails(pairs) if pairs else {}
    issues: list[Issue] = []
    for slug, date, title, body in pending:
        label = f"{date.strftime('%b')} {date.day}, {date.year}"
        MD.reset()
        rendered, stories = enhance(
            MD.convert(body),
            thumbs=thumbs,
            issue_date=date.strftime("%Y-%m-%d"),
            issue_label=label,
        )
        assert_external_links(rendered, slug)
        assert_cards(rendered, slug)
        issues.append(
            Issue(
                slug=slug,
                date=date,
                title=title,
                html=rendered,
                tldr=extract_tldr(body),
                stories=stories,
            )
        )
    issues.sort(key=lambda issue: (issue.date_iso, issue.slug), reverse=True)
    return issues


def logo_svg() -> str:
    return (
        '<svg class="logo" viewBox="0 0 36 36" aria-hidden="true">'
        '<rect width="36" height="36" rx="9" fill="#e4c27a"/>'
        '<rect x="8" y="9" width="20" height="3.1" rx="1" fill="#102a43"/>'
        '<rect x="8" y="16.2" width="13" height="3.1" rx="1" fill="#102a43"/>'
        '<rect x="8" y="23.4" width="20" height="3.1" rx="1" fill="#102a43"/>'
        "</svg>"
    )


def render_page(
    *,
    title: str,
    description: str,
    canonical: str,
    from_dir: str,
    current: str,
    body: str,
) -> str:
    css = rel_url(from_dir, "assets/style.css")
    icon = rel_url(from_dir, "assets/favicon.svg")
    script = rel_url(from_dir, "assets/favorites.js")
    home = rel_url(from_dir, "")
    archive = rel_url(from_dir, "archive/")
    favorites = rel_url(from_dir, "favorites/")
    feed = rel_url(from_dir, "feed.xml")
    description_esc = html.escape(description, quote=True)
    title_esc = html.escape(title)
    canonical_esc = html.escape(canonical, quote=True)

    def nav_link(href: str, label: str, key: str, extra: str = "") -> str:
        current_attr = ' aria-current="page"' if current == key else ""
        return f'<a href="{href}"{current_attr}>{html.escape(label)}{extra}</a>'

    nav = (
        nav_link(home, "Latest", "home")
        + nav_link(archive, "Archive", "archive")
        + nav_link(
            favorites,
            "Favorites",
            "favorites",
            ' <span class="nav-count" data-fav-count hidden></span>',
        )
        + f'<a href="{feed}">RSS</a>'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_esc}</title>
<meta name="description" content="{description_esc}">
<meta name="theme-color" content="#102a43" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#0c1824" media="(prefers-color-scheme: dark)">
<link rel="canonical" href="{canonical_esc}">
<link rel="stylesheet" href="{css}">
<link rel="icon" href="{icon}" type="image/svg+xml">
<link rel="alternate" type="application/atom+xml" title="{html.escape(SITE_NAME)}" href="{feed}">
<meta property="og:site_name" content="{html.escape(SITE_NAME)}">
<meta property="og:title" content="{title_esc}">
<meta property="og:description" content="{description_esc}">
<meta property="og:type" content="website">
<meta property="og:url" content="{canonical_esc}">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site-header">
  <div class="wrap header-inner">
    <a class="brand" href="{home}">
      {logo_svg()}
      <span class="brand-text">
        <span class="site-name">{html.escape(SITE_NAME)}</span>
        <span class="tagline">{html.escape(TAGLINE)}</span>
      </span>
    </a>
    <nav class="site-nav" aria-label="Site">
      {nav}
    </nav>
  </div>
</header>
<main id="main" class="wrap">
{body}
</main>
<footer class="site-footer">
  <div class="wrap">
    <p class="footer-label">Reliability Score</p>
    <ul class="score-key">
      <li><span class="score score-5">5</span> Official source</li>
      <li><span class="score score-4">4</span> Established outlet</li>
      <li><span class="score score-3">3</span> Analysis, vendor, or third-party copy</li>
      <li><span class="score score-2">2</span> Needs a second source</li>
    </ul>
    <p class="footer-note">Every item links to its source.</p>
  </div>
</footer>
<script src="{script}"></script>
</body>
</html>
"""


def render_issue_body(issue: Issue, from_dir: str) -> str:
    count = len(issue.stories)
    story_label = "1 story" if count == 1 else f"{count} stories"
    digest = apply_media(issue.html, media_prefix(from_dir))
    return f"""<article class="issue">
  <header class="issue-head">
    <div class="issue-kicker-row">
      <p class="kicker"><time datetime="{issue.date_iso}">{html.escape(issue.date_long)}</time><span class="kicker-dot" aria-hidden="true">·</span>{html.escape(story_label)}</p>
      <button type="button" class="filter-favs" aria-pressed="false">Show saved only</button>
    </div>
    <h1>{html.escape(issue.title)}</h1>
  </header>
  <div class="digest">
    <p class="filter-empty" hidden>No saved stories in this issue.</p>
{digest}
  </div>
</article>
"""


def render_archive(issues: list[Issue]) -> str:
    if not issues:
        items = '<p class="lede">No issues published yet.</p>'
    else:
        rows = []
        for issue in issues:
            href = rel_url("archive", issue.path)
            tldr = f'<p class="archive-tldr">{html.escape(issue.tldr)}</p>' if issue.tldr else ""
            count = len(issue.stories)
            meta = "1 story" if count == 1 else f"{count} stories"
            rows.append(
                f'<li><a class="archive-card" href="{href}">'
                f'<time datetime="{issue.date_iso}">{html.escape(issue.date_short)}</time>'
                f'<span class="archive-title">{html.escape(issue.title)}</span>'
                f"{tldr}"
                f'<span class="archive-more">{html.escape(meta)} · Read issue</span>'
                f"</a></li>"
            )
        items = '<ol class="archive-list">\n' + "\n".join(rows) + "\n</ol>"
    count = len(issues)
    lede = "Every issue, newest first." if count else "Issues will show up here."
    if count == 1:
        lede = "1 issue, newest first."
    elif count:
        lede = f"{count} issues, newest first."
    return f"""<header class="issue-head">
  <p class="kicker">Library</p>
  <h1>Archive</h1>
  <p class="lede">{html.escape(lede)}</p>
</header>
{items}
"""


def render_favorites() -> str:
    return """<header class="issue-head">
  <p class="kicker">Saved in this browser</p>
  <h1>Favorites</h1>
  <p class="lede">Favorites are saved in this browser only. They are not synced to an account, and they disappear if this browser’s site data is cleared.</p>
</header>
<p class="empty-favs" id="fav-empty">No saved stories yet. Use the star on a story to keep it here.</p>
<div id="fav-list" class="fav-list"></div>
"""


def render_feed(issues: list[Issue]) -> str:
    if issues:
        updated = issues[0].date.strftime("%Y-%m-%dT12:00:00Z")
    else:
        updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = []
    for issue in issues:
        url = f"{SITE_URL}/{issue.path}"
        summary = issue.tldr or issue.title
        content = apply_media(issue.html, f"{SITE_URL}/")
        entries.append(
            "  <entry>\n"
            f"    <title>{html.escape(issue.title)}</title>\n"
            f'    <link href="{html.escape(url, quote=True)}"/>\n'
            f"    <id>{html.escape(url)}</id>\n"
            f"    <updated>{issue.date.strftime('%Y-%m-%dT12:00:00Z')}</updated>\n"
            f"    <summary>{html.escape(summary)}</summary>\n"
            f'    <content type="html">{html.escape(content)}</content>\n'
            "  </entry>"
        )
    feed = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <title>{html.escape(SITE_NAME)}</title>\n"
        f"  <subtitle>{html.escape(TAGLINE)}</subtitle>\n"
        f'  <link href="{SITE_URL}/"/>\n'
        f'  <link rel="self" href="{SITE_URL}/feed.xml"/>\n'
        f"  <id>{SITE_URL}/</id>\n"
        f"  <updated>{updated}</updated>\n"
        + "\n".join(entries)
        + "\n</feed>\n"
    )
    ET.fromstring(feed)
    return feed


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def copy_thumbnails(issues: list[Issue]) -> None:
    names: set[str] = set()
    for issue in issues:
        for story in issue.stories:
            thumb = story.get("thumbnail") or ""
            if thumb.startswith("media/thumbs/") and thumb.endswith(".jpg"):
                names.add(thumb.rsplit("/", 1)[-1])
    if not names:
        return
    dest = DIST / "media" / "thumbs"
    dest.mkdir(parents=True, exist_ok=True)
    for name in sorted(names):
        source = cached_jpeg(name)
        if source:
            shutil.copy2(source, dest / name)


def stories_index(issues: list[Issue]) -> list[dict]:
    seen: set[str] = set()
    rows: list[dict] = []
    for issue in issues:
        for story in issue.stories:
            if story["id"] in seen:
                continue
            seen.add(story["id"])
            rows.append(story)
    return rows


def build() -> list[Issue]:
    _check_relative_urls()
    issues = load_issues()
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    if ASSETS.is_dir():
        shutil.copytree(ASSETS, DIST / "assets")
    copy_thumbnails(issues)

    if issues:
        latest = issues[0]
        description = latest.tldr or TAGLINE
        home_body = render_issue_body(latest, "")
    else:
        description = TAGLINE
        home_body = (
            '<header class="issue-head"><h1>No issues yet</h1>'
            '<p class="lede">The next digest will appear here.</p></header>'
        )

    pages = {
        DIST / "index.html": render_page(
            title=SITE_NAME,
            description=description,
            canonical=f"{SITE_URL}/",
            from_dir="",
            current="home",
            body=home_body,
        ),
        DIST / "archive" / "index.html": render_page(
            title=f"Archive — {SITE_NAME}",
            description=f"All issues of {SITE_NAME}, newest first.",
            canonical=f"{SITE_URL}/archive/",
            from_dir="archive",
            current="archive",
            body=render_archive(issues),
        ),
        DIST / "favorites" / "index.html": render_page(
            title=f"Favorites — {SITE_NAME}",
            description="Stories you saved in this browser.",
            canonical=f"{SITE_URL}/favorites/",
            from_dir="favorites",
            current="favorites",
            body=render_favorites(),
        ),
    }
    for issue in issues:
        pages[DIST / "issues" / issue.slug / "index.html"] = render_page(
            title=f"{issue.title} — {SITE_NAME}",
            description=issue.tldr or TAGLINE,
            canonical=f"{SITE_URL}/{issue.path}",
            from_dir=f"issues/{issue.slug}",
            current="",
            body=render_issue_body(issue, f"issues/{issue.slug}"),
        )
    for path, text in pages.items():
        if MEDIA_TOKEN in text:
            raise SystemExit(f"{path}: unresolved media path")
        write_text(path, text)
    write_text(DIST / "feed.xml", render_feed(issues))
    write_text(
        DIST / "stories.json",
        json.dumps(stories_index(issues), ensure_ascii=False, indent=2) + "\n",
    )
    return issues


def main() -> None:
    issues = build()
    print(f"Built {len(issues)} issue(s) → {DIST}")
    print(f"Site URL: {SITE_URL}/")
    for issue in issues:
        images = sum(1 for story in issue.stories if story.get("thumbnail"))
        print(
            f"  {issue.date_iso}  {issue.title}  "
            f"({len(issue.stories)} stories, {images} thumbnails)"
        )


if __name__ == "__main__":
    main()
