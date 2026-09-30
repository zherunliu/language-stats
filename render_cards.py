"""Render native SVG cards without external SVG templates or HTML layout."""

from datetime import datetime, timezone
from html import escape

from snapshot import Snapshot

WIDTH = 420
HEIGHT = 286
LANGUAGE_LIMIT = 6


def text(x: int, y: int, value: str, css: str = "label", anchor: str = "start") -> str:
    return (
        f'<text x="{x}" y="{y}" class="{css}" text-anchor="{anchor}">'
        f"{escape(value)}</text>"
    )


def short(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def card(title: str, description: str, height: int, content: list[str]) -> str:
    return f'''<svg id="gh-dark-mode-only" xmlns="http://www.w3.org/2000/svg"
width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" role="img"
aria-labelledby="title description">
<title id="title">{escape(title)}</title>
<desc id="description">{escape(description)}</desc>
<style>
.background {{ fill: #fff; stroke: #d0d7de; }}
text {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }}
.heading {{ fill: #0969da; font-size: 16px; font-weight: 600; }}
.label {{ fill: #57606a; font-size: 13px; }}
.value {{ fill: #24292f; font-size: 13px; font-weight: 600; }}
.note {{ fill: #57606a; font-size: 11px; }}
.track {{ fill: #eaeef2; }}
#gh-dark-mode-only:target .background {{ fill: #0d1117; stroke: #30363d; }}
#gh-dark-mode-only:target .heading {{ fill: #58a6ff; }}
#gh-dark-mode-only:target .label, #gh-dark-mode-only:target .note {{ fill: #8b949e; }}
#gh-dark-mode-only:target .value {{ fill: #c9d1d9; }}
#gh-dark-mode-only:target .track {{ fill: #21262d; }}
</style>
<rect class="background" x="0.5" y="0.5" width="419" height="{height - 1}" rx="8"/>
{chr(10).join(content)}
</svg>
'''


def updated(snapshot: Snapshot) -> str:
    date = datetime.fromisoformat(snapshot.collected_at).astimezone(timezone.utc)
    return "Updated " + date.strftime("%Y-%m-%d") + " UTC"


def overview(snapshot: Snapshot) -> str:
    heading = f"{snapshot.name}'s GitHub Statistics"
    rows = [
        ("Owned repositories (non-fork)", snapshot.repositories),
        ("Stars on owned repositories", snapshot.stars),
        ("Forks of owned repositories", snapshot.forks),
        ("All-time GitHub contributions", snapshot.contributions),
        ("Your lines changed in owned repos", snapshot.lines_changed),
        ("Owned repo views (last 14 days)", snapshot.views_14d),
    ]
    content = [text(24, 34, short(heading, 39), "heading")]
    for index, (label, value) in enumerate(rows):
        y = 67 + index * 27
        content.append(text(24, y, label))
        content.append(
            text(396, y, f"{value:,}" if value is not None else "N/A", "value", "end")
        )
    content.extend(
        [
            text(
                24,
                HEIGHT - 37,
                "N/A: disabled or inaccessible; API metrics are estimates.",
                "note",
            ),
            text(24, HEIGHT - 16, updated(snapshot), "note"),
        ]
    )
    return card(
        heading,
        "Personal GitHub statistics. "
        + "; ".join(
            f"{label}: {value if value is not None else 'unavailable'}"
            for label, value in rows
        ),
        HEIGHT,
        content,
    )


def languages(snapshot: Snapshot) -> str:
    content = [
        text(24, 34, "Languages in Owned Repositories", "heading"),
        text(24, 56, f"Top {LANGUAGE_LIMIT} shown · shares of all code bytes", "note"),
        '<rect class="track" x="24" y="72" width="372" height="8" rx="4"/>',
    ]
    total = sum(lang.size for lang in snapshot.languages)
    offset = 24.0
    for lang in snapshot.languages:
        fraction = lang.size / total
        width = 372 * fraction
        content.append(
            f'<rect x="{offset:.4f}" y="72" width="{width:.4f}" height="8" fill="{lang.color}"/>'
        )
        offset += width
    for index, lang in enumerate(snapshot.languages[:LANGUAGE_LIMIT]):
        y = 107 + index * 25
        content.extend(
            [
                f'<circle cx="29" cy="{y - 4}" r="4" fill="{lang.color}"/>',
                text(42, y, short(lang.name, 40)),
                text(396, y, f"{lang.size / total:.2%}", "value", "end"),
            ]
        )
    content.append(text(24, HEIGHT - 20, updated(snapshot), "note"))
    return card(
        "Languages in owned repositories",
        f"Top {LANGUAGE_LIMIT} languages by repository code size, not personally authored code. "
        + "; ".join(
            f"{lang.name}: {lang.size / total:.2%}"
            for lang in snapshot.languages[:LANGUAGE_LIMIT]
        ),
        HEIGHT,
        content,
    )
