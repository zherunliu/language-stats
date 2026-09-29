#!/usr/bin/env python3
"""Collect personal GitHub statistics and publish validated cards + JSON."""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

import aiohttp

import render_cards
from github_stats import GitHubAPIError, Stats
from settings import Settings
from snapshot import SCOPE, Snapshot

ROOT = Path(__file__).resolve().parent


async def collect(settings: Settings) -> Snapshot:
    token = os.getenv("ACCESS_TOKEN")
    if not token:
        raise ValueError("Set ACCESS_TOKEN before collecting GitHub statistics.")
    async with aiohttp.ClientSession() as session:
        stats = Stats(
            settings.user,
            token,
            session,
            exclude_repos=set(settings.exclude_repos),
            exclude_langs=set(settings.exclude_languages),
        )
        await stats.get_stats()
        contributions, lines, views = await asyncio.gather(
            stats.total_contributions,
            stats.lines_changed
            if settings.collect_lines_changed
            else asyncio.sleep(0, result=None),
            stats.views if settings.collect_views else asyncio.sleep(0, result=None),
        )
        data = {
            "schema_version": 1,
            "scope": SCOPE,
            "user": settings.user,
            "name": await stats.name,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "repositories": len(await stats.repos),
            "stars": await stats.stargazers,
            "forks": await stats.forks,
            "contributions": contributions,
            "lines_changed": sum(lines) if lines is not None else None,
            "views_14d": views,
            "languages": [
                {"name": name, "size": value["size"], "color": value["color"]}
                for name, value in (await stats.languages).items()
            ],
        }
    return Snapshot.from_dict(data)


def publish(snapshot: Snapshot, output_dir: Path) -> None:
    snapshot = Snapshot.from_dict(json.loads(snapshot.to_json()))
    outputs = {
        "overview.svg": render_cards.overview(snapshot),
        "languages.svg": render_cards.languages(snapshot),
        "stats.json": snapshot.to_json(),
    }
    # Complete rendering and validation before touching the published files.
    for name in ("overview.svg", "languages.svg"):
        ElementTree.fromstring(outputs[name])
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stats-", dir=output_dir) as staging:
        for filename, output in outputs.items():
            Path(staging, filename).write_text(output, encoding="utf-8")
        for filename in outputs:
            os.replace(Path(staging, filename), output_dir / filename)


async def main(
    config_path: Path = ROOT / "stats.toml",
    output_dir: Path = ROOT / "generated",
    input_path: Path | None = None,
) -> None:
    snapshot = (
        Snapshot.load(input_path)
        if input_path is not None
        else await collect(Settings.load(config_path))
    )
    publish(snapshot, output_dir)
    print(
        f"Published statistics for {snapshot.user} ({snapshot.repositories} owned repositories)."
    )


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "stats.toml")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated")
    parser.add_argument(
        "--from-json",
        type=Path,
        help="Render a saved snapshot without a token or network access.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(args.config, args.output_dir, args.from_json))
    except (GitHubAPIError, ValueError, OSError, ElementTree.ParseError) as error:
        # OSError may mention a local path, never an API response body or token.
        print(f"Statistics were not published: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    cli()
