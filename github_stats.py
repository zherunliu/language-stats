"""GitHub API collection for this personal statistics tool.

Derived from jstrieb/github-stats (GPL-3.0); adapted for owned repositories.
"""

import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp


class GitHubAPIError(RuntimeError):
    """An API response cannot be used to publish trustworthy statistics."""


def valid_count(value: object) -> bool:
    return type(value) is int and value >= 0


class Queries:
    """GitHub GraphQL and REST requests with validation and bounded retries."""

    def __init__(
        self,
        access_token: str,
        session: aiohttp.ClientSession,
        max_connections: int = 10,
    ):
        self.access_token = access_token
        self.session = session
        self.semaphore = asyncio.Semaphore(max_connections)

    async def _request(self, method: str, path: str, **kwargs) -> Tuple[int, Any]:
        """Retry transient failures without logging tokens or private repo URLs."""
        unavailable_ok = kwargs.pop("unavailable_ok", False)
        for attempt in range(3):
            try:
                async with self.semaphore:
                    async with self.session.request(
                        method,
                        f"https://api.github.com/{path.lstrip('/')}",
                        headers={
                            "Authorization": f"Bearer {self.access_token}",
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2026-03-10",
                        },
                        timeout=aiohttp.ClientTimeout(total=30),
                        **kwargs,
                    ) as response:
                        if unavailable_ok and response.status == 403:
                            try:
                                body = await response.json()
                            except (ValueError, aiohttp.ContentTypeError):
                                raise GitHubAPIError(
                                    "GitHub API returned invalid JSON."
                                ) from None
                            message = (
                                body.get("message", "")
                                if isinstance(body, dict)
                                else ""
                            )
                            if (
                                response.headers.get("x-ratelimit-remaining") == "0"
                                or response.headers.get("retry-after")
                                or "rate limit" in str(message).lower()
                            ):
                                raise GitHubAPIError(
                                    "GitHub API rate limit exceeded; keeping previous images."
                                )
                            return response.status, None
                        if unavailable_ok and response.status == 404:
                            return response.status, None
                        if response.status >= 500:
                            if attempt == 2:
                                raise GitHubAPIError(
                                    f"GitHub API returned HTTP {response.status} after retries."
                                )
                        elif response.status not in (200, 202, 204):
                            raise GitHubAPIError(
                                f"GitHub API returned HTTP {response.status}; check token permissions and rate limits."
                            )
                        elif response.status in (202, 204):
                            return response.status, None
                        else:
                            try:
                                return response.status, await response.json()
                            except (ValueError, aiohttp.ContentTypeError):
                                raise GitHubAPIError(
                                    "GitHub API returned invalid JSON."
                                ) from None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == 2:
                    raise GitHubAPIError(
                        "GitHub API request failed after retries."
                    ) from None
            await asyncio.sleep(2**attempt)
        raise GitHubAPIError("GitHub API request failed.")

    async def query(self, generated_query: str) -> Dict:
        status, result = await self._request(
            "POST", "graphql", json={"query": generated_query}
        )
        if status != 200 or not isinstance(result, dict):
            raise GitHubAPIError("GraphQL returned an invalid response.")
        if result.get("errors"):
            # Raw messages and arbitrary path segments can reveal private data.
            errors = result["errors"] if isinstance(result["errors"], list) else []
            known_types = {
                "FORBIDDEN",
                "INSUFFICIENT_SCOPES",
                "NOT_FOUND",
                "RATE_LIMITED",
                "INTERNAL",
                "UNPROCESSABLE",
            }
            known_fields = set(
                "viewer login name repositories pageInfo hasNextPage endCursor "
                "nodes nameWithOwner stargazers stargazerCount totalCount "
                "forkCount languages "
                "edges size node color contributionsCollection contributionYears "
                "contributionCalendar totalContributions".split()
            )
            codes, paths = set(), set()
            for error in errors:
                if not isinstance(error, dict):
                    continue
                code = error.get("type")
                if isinstance(code, str) and code in known_types:
                    codes.add(code)
                path = error.get("path")
                if not isinstance(path, list) or not 0 < len(path) <= 16:
                    continue
                parts = []
                for part in path:
                    if isinstance(part, str) and part in known_fields:
                        parts.append(part)
                    elif isinstance(part, str) and re.fullmatch(r"year[0-9]{4}", part):
                        parts.append("year*")
                    elif type(part) is int and part >= 0:
                        parts.append("*")
                    else:
                        break
                else:
                    paths.add(".".join(parts))
            detail = ", ".join(sorted(codes)) or "unspecified"
            fields = f" Fields: {', '.join(sorted(paths)[:5])}." if paths else ""
            hint = (
                " Check ACCESS_TOKEN resource owner, repository access and account permissions."
                if codes & {"FORBIDDEN", "INSUFFICIENT_SCOPES"}
                else ""
            )
            raise GitHubAPIError(
                f"GraphQL returned errors ({detail}); refusing partial statistics."
                f"{fields}{hint}"
            )
        if not isinstance(result.get("data"), dict) or not isinstance(
            result["data"].get("viewer"), dict
        ):
            raise GitHubAPIError("GraphQL response is missing viewer data.")
        return result

    async def query_rest(
        self, path: str, params: Optional[Dict] = None, unavailable_ok: bool = False
    ) -> Any:
        for _ in range(60):
            status, result = await self._request(
                "GET", path, params=params or {}, unavailable_ok=unavailable_ok
            )
            if status == 202:
                await asyncio.sleep(2)
                continue
            if status == 204:
                return []
            return result
        raise GitHubAPIError(
            "GitHub statistics remained pending; keeping previous images."
        )

    @staticmethod
    def repos_overview(owned_cursor: Optional[str] = None) -> str:
        after = json.dumps(owned_cursor)
        return (
            """{ viewer { login name
          repositories(first: 100, affiliations: [OWNER], isFork: false,
            orderBy: {field: UPDATED_AT, direction: DESC}, after: """
            + after
            + """) {
            pageInfo { hasNextPage endCursor }
            nodes {
              nameWithOwner
              stargazerCount
              forkCount
              languages(first: 100, orderBy: {field: SIZE, direction: DESC}) {
                totalCount
                edges { size node { name color } }
              }
            }
          }
        } }"""
        )

    @staticmethod
    def contrib_years() -> str:
        """Query years represented in the user's GitHub contribution calendar."""
        return """
query {
  viewer {
    contributionsCollection {
      contributionYears
    }
  }
}
"""

    @staticmethod
    def contribs_by_year(year: int) -> str:
        """Query one calendar year of GitHub contributions."""
        return f"""
    year{year}: contributionsCollection(
        from: "{year}-01-01T00:00:00Z",
        to: "{int(year) + 1}-01-01T00:00:00Z"
    ) {{
      contributionCalendar {{
        totalContributions
      }}
    }}
"""

    @classmethod
    def all_contribs(cls, years: List[int]) -> str:
        """Combine yearly contribution calendar queries."""
        by_years = "\n".join(map(cls.contribs_by_year, years))
        return f"""
query {{
  viewer {{
    {by_years}
  }}
}}
"""


class Stats:
    """Validated statistics for one user and their owned, non-fork repositories."""

    def __init__(
        self,
        username: str,
        access_token: str,
        session: aiohttp.ClientSession,
        exclude_repos: Optional[Set] = None,
        exclude_langs: Optional[Set] = None,
    ):
        self.username = username
        self._exclude_repos = {name.lower() for name in (exclude_repos or set())}
        self._exclude_langs = {name.lower() for name in (exclude_langs or set())}
        self.queries = Queries(access_token, session)

        self._stats_lock = asyncio.Lock()
        self._name: Optional[str] = None
        self._stargazers: Optional[int] = None
        self._forks: Optional[int] = None
        self._total_contributions: Optional[int] = None
        self._languages: Optional[Dict[str, Any]] = None
        self._repos: Optional[Set[str]] = None
        self._lines_changed: Optional[Tuple[int, int]] = None
        self._views: Optional[int] = None

    async def get_stats(self) -> None:
        """Cache one complete snapshot of owned, non-fork repositories."""
        async with self._stats_lock:
            if self._repos is not None:
                return
            stars, forks = 0, 0
            languages: Dict[str, Any] = {}
            repos: Set[str] = set()
            display_name = self.username
            cursor = None
            seen_cursors = set()
            while True:
                viewer = (await self.queries.query(Queries.repos_overview(cursor)))[
                    "data"
                ]["viewer"]
                login = viewer.get("login")
                if not isinstance(login, str) or login.lower() != self.username.lower():
                    raise GitHubAPIError(
                        "ACCESS_TOKEN does not belong to the configured stats.user."
                    )
                name = viewer.get("name")
                if name is not None and not isinstance(name, str):
                    raise GitHubAPIError("GitHub display name is malformed.")
                display_name = name or login
                connection = viewer.get("repositories")
                if not isinstance(connection, dict) or not isinstance(
                    connection.get("nodes"), list
                ):
                    raise GitHubAPIError("Repository data is missing or incomplete.")
                page_info = connection.get("pageInfo")
                if (
                    not isinstance(page_info, dict)
                    or type(page_info.get("hasNextPage")) is not bool
                ):
                    raise GitHubAPIError("Repository pagination data is missing.")
                for repo in connection["nodes"]:
                    if not isinstance(repo, dict):
                        raise GitHubAPIError(
                            "Repository data contains an invalid entry."
                        )
                    repo_name = repo.get("nameWithOwner")
                    if not isinstance(repo_name, str) or not re.fullmatch(
                        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo_name
                    ):
                        raise GitHubAPIError("Repository name is malformed.")
                    if repo_name.split("/")[0].lower() != self.username.lower():
                        raise GitHubAPIError(
                            "Repository does not belong to the configured user."
                        )
                    if repo_name in repos or repo_name.lower() in self._exclude_repos:
                        continue
                    repo_stars = repo.get("stargazerCount")
                    repo_forks = repo.get("forkCount")
                    lang_data = repo.get("languages")
                    if (
                        not valid_count(repo_stars)
                        or not valid_count(repo_forks)
                        or not isinstance(lang_data, dict)
                    ):
                        raise GitHubAPIError("Repository statistics are incomplete.")
                    edges = lang_data.get("edges")
                    if (
                        not isinstance(edges, list)
                        or type(lang_data.get("totalCount")) is not int
                        or lang_data["totalCount"] != len(edges)
                    ):
                        raise GitHubAPIError("Repository language data is incomplete.")
                    repos.add(repo_name)
                    stars += repo_stars
                    forks += repo_forks
                    seen_languages = set()
                    for edge in edges:
                        if not isinstance(edge, dict) or not isinstance(
                            edge.get("node"), dict
                        ):
                            raise GitHubAPIError(
                                "Language data contains an invalid entry."
                            )
                        lang = edge["node"].get("name")
                        size = edge.get("size")
                        if (
                            not isinstance(lang, str)
                            or not lang.strip()
                            or not valid_count(size)
                            or lang.lower() in seen_languages
                        ):
                            raise GitHubAPIError(
                                "Language data contains an invalid entry."
                            )
                        seen_languages.add(lang.lower())
                        if lang.lower() in self._exclude_langs:
                            continue
                        language = languages.setdefault(
                            lang, {"size": 0, "color": edge["node"].get("color")}
                        )
                        language["size"] += size
                if not page_info["hasNextPage"]:
                    break
                cursor = page_info.get("endCursor")
                if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                    raise GitHubAPIError("Repository pagination did not advance.")
                seen_cursors.add(cursor)
            total = sum(lang["size"] for lang in languages.values())
            if not repos or total <= 0:
                raise GitHubAPIError(
                    "No repositories or language bytes remain after filtering; keeping previous images."
                )
            for language in languages.values():
                language["prop"] = 100 * language["size"] / total
            self._name = display_name
            self._stargazers = stars
            self._forks = forks
            self._languages = languages
            self._repos = repos
            print(
                f"Validated {len(repos)} owned repositories and {len(languages)} languages."
            )

    @property
    async def name(self) -> str:
        """The authenticated user's display name."""
        if self._name is not None:
            return self._name
        await self.get_stats()
        assert self._name is not None
        return self._name

    @property
    async def stargazers(self) -> int:
        """Sum of stars on the selected owned repositories."""
        if self._stargazers is not None:
            return self._stargazers
        await self.get_stats()
        assert self._stargazers is not None
        return self._stargazers

    @property
    async def forks(self) -> int:
        """Sum of forks of the selected owned repositories."""
        if self._forks is not None:
            return self._forks
        await self.get_stats()
        assert self._forks is not None
        return self._forks

    @property
    async def languages(self) -> Dict:
        """Language byte totals across the selected owned repositories."""
        if self._languages is not None:
            return self._languages
        await self.get_stats()
        assert self._languages is not None
        return self._languages

    @property
    async def repos(self) -> Set[str]:
        """Names of the selected owned repositories, retained only in memory."""
        if self._repos is not None:
            return self._repos
        await self.get_stats()
        assert self._repos is not None
        return self._repos

    @property
    async def total_contributions(self) -> int:
        """All-time GitHub calendar contributions, independent of repo filters."""
        if self._total_contributions is not None:
            return self._total_contributions

        viewer = (await self.queries.query(Queries.contrib_years()))["data"]["viewer"]
        collection = viewer.get("contributionsCollection")
        if not isinstance(collection, dict) or not isinstance(
            collection.get("contributionYears"), list
        ):
            raise GitHubAPIError("Contribution years are missing.")
        years = collection["contributionYears"]
        if not all(type(year) is int and 2008 <= year <= 9998 for year in years) or len(
            set(years)
        ) != len(years):
            raise GitHubAPIError("Contribution years are malformed.")
        total = 0
        if years:
            viewer = (await self.queries.query(Queries.all_contribs(years)))["data"][
                "viewer"
            ]
            for year in years:
                collection = viewer.get(f"year{year}")
                calendar = (
                    collection.get("contributionCalendar")
                    if isinstance(collection, dict)
                    else None
                )
                count = (
                    calendar.get("totalContributions")
                    if isinstance(calendar, dict)
                    else None
                )
                if not valid_count(count):
                    raise GitHubAPIError(
                        "Yearly contribution statistics are incomplete."
                    )
                total += count
        self._total_contributions = total
        return total

    @property
    async def lines_changed(self) -> Optional[Tuple[int, int]]:
        """The user's additions + deletions in selected owned repositories."""
        if self._lines_changed is not None:
            return self._lines_changed
        responses = await asyncio.gather(
            *(
                self.queries.query_rest(
                    f"/repos/{repo}/stats/contributors", unavailable_ok=True
                )
                for repo in sorted(await self.repos)
            )
        )
        if any(response is None for response in responses):
            return None
        additions, deletions = 0, 0
        for response in responses:
            if not isinstance(response, list):
                raise GitHubAPIError("Contributor statistics are malformed.")
            for item in response:
                if not isinstance(item, dict):
                    raise GitHubAPIError(
                        "Contributor statistics contain an invalid entry."
                    )
                author = item.get("author")
                # Anonymous contributors cannot be attributed to this account.
                if author is None:
                    continue
                if not isinstance(author, dict) or not isinstance(
                    author.get("login"), str
                ):
                    raise GitHubAPIError("Contributor identity is malformed.")
                if author["login"].lower() != self.username.lower():
                    continue
                weeks = item.get("weeks")
                if not isinstance(weeks, list):
                    raise GitHubAPIError("Contributor weekly statistics are missing.")
                for week in weeks:
                    if (
                        not isinstance(week, dict)
                        or not valid_count(week.get("a"))
                        or not valid_count(week.get("d"))
                    ):
                        raise GitHubAPIError("Contributor line counts are malformed.")
                    additions += week["a"]
                    deletions += week["d"]
        self._lines_changed = (additions, deletions)
        return self._lines_changed

    @property
    async def views(self) -> Optional[int]:
        """Views in the 14-day window provided by GitHub, not a monthly total."""
        if self._views is not None:
            return self._views
        responses = await asyncio.gather(
            *(
                self.queries.query_rest(
                    f"/repos/{repo}/traffic/views", unavailable_ok=True
                )
                for repo in sorted(await self.repos)
            )
        )
        if any(response is None for response in responses):
            return None
        total = 0
        for response in responses:
            if not isinstance(response, dict) or not isinstance(
                response.get("views"), list
            ):
                raise GitHubAPIError("Traffic statistics are malformed.")
            for view in response["views"]:
                if not isinstance(view, dict) or not valid_count(view.get("count")):
                    raise GitHubAPIError("Traffic counts are malformed.")
                total += view["count"]
        self._views = total
        return total
