"""Validated aggregate data shared by online collection and offline rendering."""

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

SCOPE = {
    "repositories": "owned_non_fork_visible_to_token",
    "languages": "repository_bytes_after_exclusions",
    "contributions": "github_calendar_all_time",
}


def count(value: object, label: str, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError(f"Snapshot {label} must be a non-negative integer.")
    return value


@dataclass(frozen=True)
class Language:
    name: str
    size: int
    color: str


@dataclass(frozen=True)
class Snapshot:
    user: str
    name: str
    collected_at: str
    repositories: int
    stars: int
    forks: int
    contributions: int
    lines_changed: int | None
    views_14d: int | None
    languages: tuple[Language, ...]

    def to_json(self) -> str:
        return (
            json.dumps(
                {"schema_version": 1, "scope": SCOPE, **asdict(self)},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )

    @classmethod
    def from_dict(cls, data: object) -> "Snapshot":
        if (
            not isinstance(data, dict)
            or type(data.get("schema_version")) is not int
            or data["schema_version"] != 1
        ):
            raise ValueError("Unsupported statistics snapshot schema.")
        required = set(cls.__dataclass_fields__)
        if required - set(data):
            raise ValueError("Snapshot is missing required statistics fields.")
        if data.get("scope") != SCOPE:
            raise ValueError("Snapshot statistics scope does not match this tool.")
        user, name = data.get("user"), data.get("name")
        if not isinstance(user, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", user
        ):
            raise ValueError("Snapshot user is invalid.")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Snapshot display name is missing.")
        timestamp = data.get("collected_at")
        if not isinstance(timestamp, str):
            raise ValueError("Snapshot collection time is missing.")
        try:
            parsed_time = datetime.fromisoformat(timestamp)
        except ValueError:
            raise ValueError("Snapshot collection time is invalid.") from None
        if parsed_time.tzinfo is None:
            raise ValueError("Snapshot collection time must include a time zone.")
        counts = {
            key: count(data.get(key), key)
            for key in ("repositories", "stars", "forks", "contributions")
        }
        if not counts["repositories"]:
            raise ValueError("Snapshot has no repositories.")
        languages = data.get("languages")
        if not isinstance(languages, list) or not languages:
            raise ValueError("Snapshot language data is missing.")
        result = []
        seen = set()
        for item in languages:
            if not isinstance(item, dict):
                raise ValueError("Snapshot language entry is invalid.")
            lang = item.get("name")
            if not isinstance(lang, str) or not lang.strip() or lang.lower() in seen:
                raise ValueError("Snapshot language name is invalid or duplicated.")
            seen.add(lang.lower())
            size = count(item.get("size"), "language size")
            color = item.get("color")
            if not isinstance(color, str) or not re.fullmatch(
                r"#[0-9a-fA-F]{6}", color
            ):
                color = "#8b949e"
            result.append(Language(lang, size, color))
        if sum(lang.size for lang in result) <= 0:
            raise ValueError("Snapshot contains no language bytes.")
        return cls(
            user=user,
            name=name,
            collected_at=timestamp,
            **counts,
            lines_changed=count(
                data.get("lines_changed"), "lines_changed", optional=True
            ),
            views_14d=count(data.get("views_14d"), "views_14d", optional=True),
            languages=tuple(sorted(result, key=lambda lang: (-lang.size, lang.name))),
        )

    @classmethod
    def load(cls, path: Path) -> "Snapshot":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
