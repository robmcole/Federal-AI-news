#!/usr/bin/env python3
"""Build the Federal AI News site from issues/*.md.

Adding a digest is one new file: issues/YYYY-MM-DD.md. This script discovers
every issue, so the home page, archive, permalinks, and feed need no hand edits.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent
ISSUES_DIR = ROOT / "issues"
DIST = ROOT / "dist"
ASSETS = ROOT / "assets"

SITE_NAME = "Federal AI News"
TAGLINE = "A weekday TLDR of AI and tech across the U.S. federal government"
SITE_URL = os.environ.get(
    "SITE_URL", "https://robmcole.github.io/Federal-AI-news"
).rstrip("/")

FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.md$")
FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
TLDR_RE = re.compile(r"^\*\*TL;DR:\*\*\s*(.+)$", re.MULTILINE)
ITEM_RE = re.compile(
    r'<p><strong>(<a href="[^"]+">.*?</a>)</strong>\s*(.*?)</p>',
    re.DOTALL,
)
EXTERNAL_LINK_RE = re.compile(r'<a href="([^"]+)"([^>]*)>')
SCORE_RE = re.compile(r"\s*·?\s*Reliability Score:\s*([1-5])/5")

MD = markdown.Markdown(extensions=["extra", "sane_lists"])


@dataclass(frozen=True)
class Issue:
    slug: str
    date: datetime
    title: str
    html: str
    tldr: str

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


def _check_relative_urls() -> None:
    cases = {
        ("", "assets/style.css"): "assets/style.css",
        ("", "archive/"): "archive/",
        ("", "issues/2026-10-02/"): "issues/2026-10-02/",
        ("", "feed.xml"): "feed.xml",
        ("", ""): "./",
        ("archive", ""): "../",
        ("archive", "assets/style.css"): "../assets/style.css",
        ("archive", "issues/2026-10-02/"): "../issues/2026-10-02/",
        ("archive", "feed.xml"): "../feed.xml",
        ("archive", "archive/"): "./",
        ("issues/2026-10-02", ""): "../../",
        ("issues/2026-10-02", "archive/"): "../../archive/",
        ("issues/2026-10-02", "assets/style.css"): "../../assets/style.css",
        ("issues/2026-10-02", "feed.xml"): "../../feed.xml",
    }
    for (from_dir, target), expected in cases.items():
        got = rel_url(from_dir, target)
        if got != expected:
            raise SystemExit(
                f"rel_url({from_dir!r}, {target!r}) = {got!r}, expected {expected!r}"
            )


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
        f'<span class="score score-{score}" '
        f'title="Reliability score {score} out of 5">{score}/5</span>'
    )


def enhance(article_html: str) -> str:
    """Turn digest conventions into callouts, section structure, and score badges.

    Items in the source files are often one Markdown paragraph (title, summary,
    and the italic source line, with no blank lines). Both that shape and a
    blank-line-separated shape are handled.
    """
    article_html = SCORE_RE.sub(lambda m: " " + score_badge(m.group(1)), article_html)
    article_html = re.sub(r"<em>(Source:)", r'<em class="item-meta">\1', article_html)
    article_html = re.sub(
        r"<em>(Primary document:)",
        r'<em class="item-meta item-primary">\1',
        article_html,
    )

    def split_item(match: re.Match[str]) -> str:
        title_link = match.group(1)
        rest = match.group(2)
        metas = re.findall(r"<em class=\"item-meta[^\"]*\">.*?</em>", rest, flags=re.DOTALL)
        body = rest
        for meta in metas:
            body = body.replace(meta, "", 1)
        body = body.strip()
        parts = [f'<h3 class="item-title">{title_link}</h3>']
        if body:
            parts.append(f"<p>{body}</p>")
        for meta in metas:
            meta_match = re.match(r'<em class="([^"]+)">(.*)</em>', meta, re.DOTALL)
            if not meta_match:
                continue
            parts.append(
                f'<p class="{meta_match.group(1)}">{meta_match.group(2).strip()}</p>'
            )
        return "\n".join(parts)

    article_html = ITEM_RE.sub(split_item, article_html)
    article_html = re.sub(
        r'<p><em class="(item-meta[^"]*)">(.*?)</em></p>',
        r'<p class="\1">\2</p>',
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
    return externalize(article_html)


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
    issues: list[Issue] = []
    seen: set[str] = set()
    for path in sorted(ISSUES_DIR.glob("*.md")):
        match = FILENAME_RE.match(path.name)
        if not match:
            raise SystemExit(
                f"{path.name}: issue files must be named YYYY-MM-DD.md"
            )
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
        MD.reset()
        rendered = enhance(MD.convert(body))
        assert_external_links(rendered, slug)
        issues.append(
            Issue(
                slug=slug,
                date=date,
                title=meta["title"],
                html=rendered,
                tldr=extract_tldr(body),
            )
        )
    issues.sort(key=lambda issue: (issue.date_iso, issue.slug), reverse=True)
    return issues


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
    home = rel_url(from_dir, "")
    archive = rel_url(from_dir, "archive/")
    feed = rel_url(from_dir, "feed.xml")
    description_esc = html.escape(description, quote=True)
    title_esc = html.escape(title)
    canonical_esc = html.escape(canonical, quote=True)

    def nav_link(href: str, label: str, key: str) -> str:
        current_attr = ' aria-current="page"' if current == key else ""
        return f'<a href="{href}"{current_attr}>{html.escape(label)}</a>'

    nav = (
        nav_link(home, "Latest", "home")
        + nav_link(archive, "Archive", "archive")
        + f'<a href="{feed}">RSS</a>'
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_esc}</title>
<meta name="description" content="{description_esc}">
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
    <div class="brand">
      <a class="site-name" href="{home}">{html.escape(SITE_NAME)}</a>
      <p class="tagline">{html.escape(TAGLINE)}</p>
    </div>
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
    <ul class="score-key">
      <li><span class="score score-5">5</span> Official source</li>
      <li><span class="score score-4">4</span> Established outlet</li>
      <li><span class="score score-3">3</span> Analysis, vendor, or third-party copy</li>
      <li><span class="score score-2">2</span> Needs a second source</li>
    </ul>
    <p class="footer-note">Every item links to its source.</p>
  </div>
</footer>
</body>
</html>
"""


def render_issue_body(issue: Issue) -> str:
    return f"""<article class="issue">
  <header class="issue-head">
    <p class="kicker"><time datetime="{issue.date_iso}">{html.escape(issue.date_long)}</time></p>
    <h1>{html.escape(issue.title)}</h1>
  </header>
  <div class="digest">
{issue.html}
  </div>
</article>
"""


def render_archive(issues: list[Issue]) -> str:
    if not issues:
        items = "<p class=\"lede\">No issues published yet.</p>"
    else:
        rows = []
        for issue in issues:
            href = rel_url("archive", issue.path)
            rows.append(
                "<li>"
                f'<time datetime="{issue.date_iso}">{html.escape(issue.date_short)}</time>'
                f'<a href="{href}">{html.escape(issue.title)}</a>'
                "</li>"
            )
        items = "<ul class=\"archive-list\">\n" + "\n".join(rows) + "\n</ul>"
    return f"""<header class="issue-head">
  <h1>Archive</h1>
  <p class="lede">Every issue, newest first.</p>
</header>
{items}
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
        entries.append(
            "  <entry>\n"
            f"    <title>{html.escape(issue.title)}</title>\n"
            f'    <link href="{html.escape(url, quote=True)}"/>\n'
            f"    <id>{html.escape(url)}</id>\n"
            f"    <updated>{issue.date.strftime('%Y-%m-%dT12:00:00Z')}</updated>\n"
            f"    <summary>{html.escape(summary)}</summary>\n"
            f'    <content type="html">{html.escape(issue.html)}</content>\n'
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


def build() -> list[Issue]:
    _check_relative_urls()
    issues = load_issues()
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    if ASSETS.is_dir():
        shutil.copytree(ASSETS, DIST / "assets")

    if issues:
        latest = issues[0]
        description = latest.tldr or TAGLINE
        home_body = render_issue_body(latest)
    else:
        description = TAGLINE
        home_body = (
            '<header class="issue-head"><h1>No issues yet</h1>'
            '<p class="lede">The next digest will appear here.</p></header>'
        )

    write_text(
        DIST / "index.html",
        render_page(
            title=SITE_NAME,
            description=description,
            canonical=f"{SITE_URL}/",
            from_dir="",
            current="home",
            body=home_body,
        ),
    )
    write_text(
        DIST / "archive" / "index.html",
        render_page(
            title=f"Archive — {SITE_NAME}",
            description=f"All issues of {SITE_NAME}, newest first.",
            canonical=f"{SITE_URL}/archive/",
            from_dir="archive",
            current="archive",
            body=render_archive(issues),
        ),
    )
    for issue in issues:
        write_text(
            DIST / "issues" / issue.slug / "index.html",
            render_page(
                title=f"{issue.title} — {SITE_NAME}",
                description=issue.tldr or TAGLINE,
                canonical=f"{SITE_URL}/{issue.path}",
                from_dir=f"issues/{issue.slug}",
                current="",
                body=render_issue_body(issue),
            ),
        )
    write_text(DIST / "feed.xml", render_feed(issues))
    return issues


def main() -> None:
    issues = build()
    print(f"Built {len(issues)} issue(s) → {DIST}")
    print(f"Site URL: {SITE_URL}/")
    for issue in issues:
        print(f"  {issue.date_iso}  {issue.title}")


if __name__ == "__main__":
    main()
