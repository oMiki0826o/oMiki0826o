#!/usr/bin/env python3
"""Build the public GitHub profile README and cached SVG cards.

All data comes from GitHub GraphQL. No invented pinned repositories, grades,
or numerical placeholders are emitted into the generated statistics.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

LOGIN = "oMiki0826o"
START = "<!-- PINNED_PROJECTS_START -->"
END = "<!-- PINNED_PROJECTS_END -->"
GITHUB = "https://api.github.com/graphql"
QUERY = """query($login: String!, $after: String, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    pinnedItems(first: 6, types: REPOSITORY) {
      nodes { ... on Repository { name nameWithOwner url description } }
    }
    contributionsCollection(from: $from, to: $to) { totalCommitContributions }
    pullRequests(first: 1) { totalCount }
    issues(first: 1) { totalCount }
    repositoriesContributedTo(first: 1) { totalCount }
    repositories(first: 100, after: $after, privacy: PUBLIC, ownerAffiliations: [OWNER]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        name isFork isArchived stargazerCount
        languages(first: 25, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}"""

# GitHub Linguist colors are returned by GraphQL; these are used if a color is null.
FALLBACK_COLORS = {
    "Python": "#3572A5", "TypeScript": "#3178C6", "JavaScript": "#f1e05a",
    "Java": "#b07219", "C": "#555555", "C++": "#f34b7d",
    "HTML": "#e34c26", "CSS": "#563d7c", "Go": "#00ADD8",
    "Rust": "#dea584", "Shell": "#89e051", "Vue": "#41b883",
}


def graph_request(token: str, login: str, after: str | None, now: datetime) -> dict:
    year = now.year
    payload = json.dumps({"query": QUERY, "variables": {
        "login": login, "after": after,
        "from": f"{year}-01-01T00:00:00Z",
        "to": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }}).encode("utf-8")
    req = Request(GITHUB, data=payload, method="POST", headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "Content-Type": "application/json", "User-Agent": "miki-profile-updater/1.0",
    })
    with urlopen(req, timeout=30) as response:
        result = json.load(response)
    if result.get("errors"):
        raise RuntimeError("GraphQL returned errors: " + json.dumps(result["errors"], ensure_ascii=False))
    user = (result.get("data") or {}).get("user")
    if not user:
        raise ValueError("GitHub did not return the requested user")
    return user


def fetch_profile(token: str, login: str, now: datetime) -> dict:
    repos: list[dict] = []
    after = None
    first = None
    for page in range(20):
        data = graph_request(token, login, after, now)
        if first is None:
            first = data
        collection = data.get("repositories") or {}
        repos.extend(collection.get("nodes") or [])
        info = collection.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        next_cursor = info.get("endCursor")
        if not next_cursor or next_cursor == after:
            raise ValueError("Invalid GitHub repository pagination cursor")
        after = next_cursor
    else:
        raise ValueError("GitHub repository list exceeds supported page limit")
    assert first is not None
    first["repositories"]["nodes"] = repos
    return first


def clean_cell(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = html.escape(text, quote=False)
    for char in ("\\", "|", "[", "]"):
        text = text.replace(char, "\\" + char)
    return text


def pinned_table(user: dict, login: str = LOGIN) -> str:
    nodes = ((user.get("pinnedItems") or {}).get("nodes") or [])
    pins = [p for p in nodes if p and p.get("name") and p.get("url")]
    if not pins:
        raise ValueError("No pinned GitHub repositories returned; previous README is retained")
    rows = ["| 專案 | 簡介 |", "| :--- | :--- |"]
    for pin in pins[:5]:
        repo_name = pin["name"]
        full_name = pin.get("nameWithOwner", "")
        if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", full_name or ""):
            raise ValueError("Invalid GitHub repository name")
        expected = "https://github.com/" + full_name
        if pin["url"].rstrip("/").casefold() != expected.casefold():
            raise ValueError("Invalid or unexpected GitHub repository URL")
        rows.append(f"| [{clean_cell(repo_name)}]({expected}) | {clean_cell(pin.get('description')) or '—'} |")
    return "\n".join(rows)


def replace_pins(readme: str, table: str) -> str:
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("Expected exactly one pair of pinned-project markers")
    a, b = readme.index(START) + len(START), readme.index(END)
    if a > b:
        raise ValueError("Reversed pinned-project markers")
    return readme[:a] + "\n" + table + "\n" + readme[b:]


def color_for(language: str, raw: object) -> str:
    if isinstance(raw, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", raw):
        return raw
    return FALLBACK_COLORS.get(language, "#8b949e")


def language_totals(user: dict, login: str = LOGIN) -> list[tuple[str, int, str]]:
    total: dict[str, int] = defaultdict(int)
    colors: dict[str, str] = {}
    for repo in ((user.get("repositories") or {}).get("nodes") or []):
        if not repo or repo.get("isFork") or repo.get("isArchived") or repo.get("name") == login:
            continue
        for edge in ((repo.get("languages") or {}).get("edges") or []):
            node = edge.get("node") or {}
            language, size = node.get("name"), edge.get("size")
            if not isinstance(language, str) or not isinstance(size, int) or size < 0:
                continue
            total[language] += size
            colors[language] = color_for(language, node.get("color"))
    if not total or sum(total.values()) == 0:
        raise ValueError("No public repository language data returned")
    return [(name, count, colors[name]) for name, count in sorted(total.items(), key=lambda it: (-it[1], it[0]))]


def svg_stats(user: dict, year: int, *, dark: bool) -> str:
    fg = "#e6edf3" if dark else "#24292f"
    title = "#a6c4ae" if dark else "#2d604c"
    muted = "#9da7b3" if dark else "#57606a"
    bg = "#0d1117" if dark else "#ffffff"
    edge = "#33473e" if dark else "#d0d7de"
    props = [
        ("Total Stars:", sum(int(x.get("stargazerCount") or 0) for x in ((user.get("repositories") or {}).get("nodes") or []))),
        (f"Total Commits ({year}):", int((user.get("contributionsCollection") or {})["totalCommitContributions"])),
        ("Total PRs:", int((user.get("pullRequests") or {})["totalCount"])),
        ("Total Issues:", int((user.get("issues") or {})["totalCount"])),
        ("Contributed to:", int((user.get("repositoriesContributedTo") or {})["totalCount"])),
    ]
    elements = [f'<svg xmlns="http://www.w3.org/2000/svg" width="700" height="260" viewBox="0 0 700 260" role="img" aria-label="GitHub statistics for {year}">',
                f'<rect x="1" y="1" width="698" height="258" rx="9" fill="{bg}" stroke="{edge}" />',
                f'<text x="30" y="43" font-family="Arial,sans-serif" font-size="25" font-weight="700" fill="{title}">Miki’s GitHub Statistics</text>']
    for index, (label, number) in enumerate(props):
        y = 81 + index * 32
        elements.append(f'<text x="30" y="{y}" font-family="Arial,sans-serif" font-size="17" font-weight="600" fill="{fg}">{escape(label)}</text>')
        elements.append(f'<text x="307" y="{y}" font-family="Arial,sans-serif" font-size="18" fill="{fg}">{number:,}</text>')
    elements += [
        f'<circle cx="590" cy="135" r="65" fill="none" stroke="{edge}" stroke-width="12"/>',
        f'<circle cx="590" cy="135" r="65" fill="none" stroke="{title}" stroke-width="12" stroke-dasharray="260 410" transform="rotate(-90 590 135)" />',
        f'<text x="590" y="142" text-anchor="middle" font-family="Arial,sans-serif" font-size="29" font-weight="700" fill="{fg}">{year}</text>',
        f'<text x="590" y="166" text-anchor="middle" font-family="Arial,sans-serif" font-size="12" fill="{muted}">PUBLIC DATA</text>',
        '</svg>']
    return "\n".join(elements) + "\n"


def svg_languages(languages: list[tuple[str, int, str]], *, dark: bool) -> str:
    fg = "#e6edf3" if dark else "#24292f"
    title = "#a6c4ae" if dark else "#2d604c"
    muted = "#9da7b3" if dark else "#57606a"
    bg = "#0d1117" if dark else "#ffffff"
    edge = "#33473e" if dark else "#d0d7de"
    track = "#212b2a" if dark else "#eef2ef"
    rows = languages[:6]
    subtotal = sum(x[1] for x in languages)
    components = [f'<svg xmlns="http://www.w3.org/2000/svg" width="700" height="325" viewBox="0 0 700 325" role="img" aria-label="GitHub top languages using Linguist colors">',
                  f'<rect x="1" y="1" width="698" height="323" rx="9" fill="{bg}" stroke="{edge}"/>',
                  f'<text x="30" y="43" font-family="Arial,sans-serif" font-size="25" font-weight="700" fill="{title}">Most Used Languages</text>']
    for index, (name, size, color) in enumerate(rows):
        percent = 100 * size / subtotal
        y = 78 + index * 35
        components += [
            f'<circle cx="37" cy="{y-5}" r="6" fill="{color}"/>',
            f'<text x="52" y="{y}" font-family="Arial,sans-serif" font-size="16" fill="{fg}">{escape(name)}</text>',
            f'<rect x="255" y="{y-12}" width="335" height="14" rx="7" fill="{track}"/>',
            f'<rect x="255" y="{y-12}" width="{max(1, round(335 * size / subtotal))}" height="14" rx="7" fill="{color}"/>',
            f'<text x="664" y="{y}" text-anchor="end" font-family="Arial,sans-serif" font-size="16" fill="{fg}">{percent:.1f}%</text>',
        ]
    components += [f'<text x="30" y="305" font-family="Arial,sans-serif" font-size="12" fill="{muted}">Public, non-fork, active repositories · measured by language bytes</text>', '</svg>']
    return "\n".join(components) + "\n"


def build(root: Path, user: dict, now: datetime) -> list[str]:
    readme_file = root / "README.md"
    before = readme_file.read_text(encoding="utf-8")
    after = replace_pins(before, pinned_table(user))
    languages = language_totals(user)
    outputs = {
        root / "assets" / "stats-light.svg": svg_stats(user, now.year, dark=False),
        root / "assets" / "stats-dark.svg": svg_stats(user, now.year, dark=True),
        root / "assets" / "top-langs-light.svg": svg_languages(languages, dark=False),
        root / "assets" / "top-langs-dark.svg": svg_languages(languages, dark=True),
        readme_file: after,
    }
    changed = []
    for path, content in outputs.items():
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            changed.append(str(path.relative_to(root)))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", type=Path)
    parser.add_argument("--fixture", type=Path, help="Offline GitHub GraphQL user fixture")
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    try:
        if args.fixture:
            user = json.loads(args.fixture.read_text(encoding="utf-8"))
        else:
            token = os.getenv("GITHUB_TOKEN")
            if not token:
                raise RuntimeError("GITHUB_TOKEN is required")
            user = fetch_profile(token, LOGIN, now)
        changed = build(args.root, user, now)
        print("Updated:" if changed else "Unchanged:", ", ".join(changed) or "README and SVG assets")
        return 0
    except (HTTPError, URLError, RuntimeError, ValueError, KeyError, OSError, TypeError) as error:
        print(f"Profile generation failed without changing existing output: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
