"""Personal statistics settings; credentials remain in the environment."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

import tomllib


@dataclass(frozen=True)
class Settings:
    user: str
    exclude_repos: frozenset[str] = frozenset()
    exclude_languages: frozenset[str] = frozenset()
    collect_lines_changed: bool = True
    collect_views: bool = True

    @classmethod
    def load(cls, path: Path) -> "Settings":
        with path.open("rb") as stream:
            document = tomllib.load(stream)
        if set(document) != {"stats"} or not isinstance(document["stats"], dict):
            raise ValueError("Configuration must contain a [stats] table.")
        options = document["stats"]
        allowed = {
            "user",
            "exclude_repos",
            "exclude_languages",
            "collect_lines_changed",
            "collect_views",
        }
        if set(options) - allowed:
            raise ValueError("Configuration contains unknown settings.")
        user = options.get("user")
        if not isinstance(user, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", user
        ):
            raise ValueError("stats.user must be a GitHub username.")

        def exclusions(key: str, env: str) -> frozenset[str]:
            values = options.get(key, [])
            if not isinstance(values, list) or not all(
                isinstance(value, str) and value.strip() for value in values
            ):
                raise ValueError(f"stats.{key} must be a list of non-empty strings.")
            # Existing secrets still work and keep private repo names out of Git.
            values = values + os.getenv(env, "").split(",")
            return frozenset(value.strip().lower() for value in values if value.strip())

        flags = {}
        for key in ("collect_lines_changed", "collect_views"):
            value = options.get(key, True)
            if type(value) is not bool:
                raise ValueError(f"stats.{key} must be true or false.")
            flags[key] = value
        return cls(
            user=user,
            exclude_repos=exclusions("exclude_repos", "EXCLUDED"),
            exclude_languages=exclusions("exclude_languages", "EXCLUDED_LANGS"),
            **flags,
        )
