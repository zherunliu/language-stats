import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

import generate_images
import render_cards
from github_stats import GitHubAPIError, Queries, Stats
from snapshot import Snapshot


def repository(name="rico/project", languages=None):
    languages = [("Python", 60), ("TypeScript", 40)] if languages is None else languages
    return {
        "nameWithOwner": name,
        "stargazers": {"totalCount": 2},
        "forkCount": 1,
        "languages": {
            "totalCount": len(languages),
            "edges": [
                {"size": size, "node": {"name": lang, "color": "#123456"}}
                for lang, size in languages
            ],
        },
    }


def connection(repos, next_page=False, cursor=None):
    return {"nodes": repos, "pageInfo": {"hasNextPage": next_page, "endCursor": cursor}}


def page(repos, next_page=False, cursor=None):
    viewer = {
        "login": "rico",
        "name": "Rico & friends",
        "repositories": connection(repos, next_page, cursor),
    }
    return {"data": {"viewer": viewer}}


def response(status=200, payload=None):
    result = MagicMock()
    result.status = status
    result.headers = {}
    result.json = AsyncMock(return_value=payload)
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=result)
    context.__aexit__ = AsyncMock(return_value=False)
    return context, result


class APIErrorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = MagicMock()
        self.queries = Queries("test-token", self.session)

    async def test_partial_graphql_error_is_rejected_without_leaking_response(self):
        payload = page([None])
        payload["errors"] = [
            {"type": "FORBIDDEN", "message": "secret-org/private-repo"}
        ]
        self.session.request.return_value = response(payload=payload)[0]
        with self.assertRaises(GitHubAPIError) as error:
            await self.queries.query("query")
        self.assertIn("FORBIDDEN", str(error.exception))
        self.assertNotIn("secret-org", str(error.exception))

    async def test_non_success_http_status_is_not_empty_data(self):
        for status in (401, 403, 404, 429):
            with self.subTest(status=status):
                self.session.request.return_value = response(status)[0]
                with self.assertRaises(GitHubAPIError):
                    await self.queries.query("query")

    async def test_missing_viewer_and_invalid_json_are_rejected(self):
        for payload in ({}, {"data": {"viewer": None}}, []):
            self.session.request.return_value = response(payload=payload)[0]
            with self.assertRaises(GitHubAPIError):
                await self.queries.query("query")
        context, result = response()
        result.json.side_effect = ValueError("private response")
        self.session.request.return_value = context
        with self.assertRaisesRegex(GitHubAPIError, "invalid JSON"):
            await self.queries.query("query")

    async def test_malformed_optional_permission_response_has_a_safe_error(self):
        context, result = response(403)
        result.json.side_effect = ValueError("private API response")
        self.session.request.return_value = context
        with self.assertRaisesRegex(GitHubAPIError, "invalid JSON") as error:
            await self.queries.query_rest("optional", unavailable_ok=True)
        self.assertNotIn("private", str(error.exception))

    async def test_transient_failure_can_recover(self):
        self.session.request.side_effect = [
            response(503)[0],
            response(payload=page([repository()]))[0],
        ]
        with patch("github_stats.asyncio.sleep", new_callable=AsyncMock):
            result = await self.queries.query("query")
        self.assertEqual(result["data"]["viewer"]["login"], "rico")
        self.assertEqual(self.session.request.call_count, 2)

    async def test_network_failures_have_bounded_retries_and_safe_messages(self):
        self.session.request.side_effect = aiohttp.ClientConnectionError("private URL")
        with patch("github_stats.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaises(GitHubAPIError) as error:
                await self.queries.query("query")
        self.assertEqual(self.session.request.call_count, 3)
        self.assertNotIn("private URL", str(error.exception))

    async def test_pending_statistics_do_not_become_zero(self):
        self.session.request.return_value = response(202)[0]
        with patch("github_stats.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaisesRegex(GitHubAPIError, "pending"):
                await self.queries.query_rest("repos/rico/project/stats/contributors")
        self.assertEqual(self.session.request.call_count, 60)

    async def test_optional_permissions_and_real_empty_stats_are_distinct(self):
        for status in (403, 404):
            self.session.request.return_value = response(status)[0]
            self.assertIsNone(
                await self.queries.query_rest("optional", unavailable_ok=True)
            )
        self.session.request.return_value = response(204)[0]
        self.assertEqual(await self.queries.query_rest("contributors"), [])

    async def test_optional_rate_limit_is_not_mistaken_for_missing_permission(self):
        for message, headers in (
            ("API rate limit exceeded", {}),
            ("Forbidden", {"x-ratelimit-remaining": "0"}),
            ("Forbidden", {"retry-after": "60"}),
        ):
            context, result = response(403, {"message": message})
            result.headers = headers
            self.session.request.return_value = context
            with self.assertRaisesRegex(GitHubAPIError, "rate limit"):
                await self.queries.query_rest("optional", unavailable_ok=True)


class StatsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stats = Stats("rico", "test-token", None)
        self.stats.queries.query = AsyncMock(return_value=page([repository()]))

    async def test_concurrent_consumers_share_one_complete_snapshot(self):
        langs, name, stars = await asyncio.gather(
            self.stats.languages, self.stats.name, self.stats.stargazers
        )
        self.assertEqual(name, "Rico & friends")
        self.assertEqual(stars, 2)
        self.assertEqual(langs["Python"]["prop"], 60)
        self.assertEqual(self.stats.queries.query.await_count, 1)
        query = self.stats.queries.query.call_args.args[0]
        self.assertNotIn("repositoriesContributedTo", query)

    async def test_failed_later_page_does_not_leave_partial_cached_stats(self):
        first = page([repository()], True, "next")
        self.stats.queries.query.side_effect = [
            first,
            GitHubAPIError("failed"),
            page([repository()]),
        ]
        with self.assertRaises(GitHubAPIError):
            await self.stats.get_stats()
        self.assertIsNone(self.stats._repos)
        self.assertIsNone(self.stats._languages)
        self.assertEqual(await self.stats.repos, {"rico/project"})

    async def test_owned_repositories_are_paginated_without_external_queries(self):
        self.stats.queries.query.side_effect = [
            page([repository()], True, "next"),
            page([repository("rico/second")]),
        ]
        self.assertEqual(await self.stats.repos, {"rico/project", "rico/second"})
        queries = [call.args[0] for call in self.stats.queries.query.call_args_list]
        self.assertIn('after: "next"', queries[1])
        for query in queries:
            self.assertIn("affiliations: [OWNER]", query)
            self.assertIn("isFork: false", query)
            self.assertNotIn("repositoriesContributedTo", query)

    async def test_repeated_cursor_cannot_loop_forever(self):
        self.stats.queries.query.side_effect = [
            page([repository()], True, "first"),
            page([repository("rico/second")], True, "second"),
            page([repository("rico/third")], True, "first"),
        ]
        with self.assertRaisesRegex(GitHubAPIError, "pagination"):
            await self.stats.get_stats()
        self.assertIsNone(self.stats._repos)

    async def test_repository_filters_are_case_insensitive(self):
        self.stats._exclude_repos = {"rico/ignored"}
        self.stats.queries.query.return_value = page(
            [
                repository(),
                repository("rico/Ignored"),
            ]
        )
        self.assertEqual(await self.stats.repos, {"rico/project"})

    async def test_negative_or_boolean_core_counts_are_rejected(self):
        for value in (-1, True, "2"):
            item = repository()
            item["stargazers"]["totalCount"] = value
            self.stats.queries.query.return_value = page([item])
            with self.assertRaises(GitHubAPIError):
                await self.stats.get_stats()

    async def test_malformed_optional_metrics_do_not_silently_become_zero(self):
        await self.stats.get_stats()
        self.stats.queries.query_rest = AsyncMock(
            return_value=[{"author": {"login": "rico"}, "weeks": [{"d": 2}]}]
        )
        with self.assertRaises(GitHubAPIError):
            await self.stats.lines_changed
        self.stats.queries.query_rest.return_value = {"views": [{"count": -1}]}
        with self.assertRaises(GitHubAPIError):
            await self.stats.views

    async def test_invalid_and_empty_repository_snapshots_are_rejected(self):
        invalid = [
            page([]),
            page([None]),
            page([repository(languages=[])]),
            {"data": {"viewer": {"login": "rico"}}},
        ]
        wrong_user = page([repository()])
        wrong_user["data"]["viewer"]["login"] = "someone-else"
        invalid.append(wrong_user)
        bad_languages = repository()
        bad_languages["languages"]["totalCount"] = 3
        invalid.append(page([bad_languages]))
        for data in invalid:
            with self.subTest(data=data):
                self.stats.queries.query.return_value = data
                with self.assertRaises(GitHubAPIError):
                    await self.stats.get_stats()
                self.assertIsNone(self.stats._repos)

    async def test_all_filtered_languages_cannot_publish_an_empty_card(self):
        self.stats._exclude_langs = {"python", "typescript"}
        with self.assertRaises(GitHubAPIError):
            await self.stats.languages

    async def test_languages_after_the_first_ten_are_included(self):
        languages = [(f"Language{i}", 1) for i in range(15)]
        self.stats.queries.query.return_value = page([repository(languages=languages)])
        result = await self.stats.languages
        self.assertEqual(len(result), 15)
        self.assertAlmostEqual(sum(item["prop"] for item in result.values()), 100)

    async def test_incomplete_yearly_contributions_cannot_be_cached_as_zero(self):
        years = {
            "data": {
                "viewer": {"contributionsCollection": {"contributionYears": [2026]}}
            }
        }
        self.stats.queries.query.side_effect = [years, {"data": {"viewer": {}}}]
        with self.assertRaises(GitHubAPIError):
            await self.stats.total_contributions
        self.assertIsNone(self.stats._total_contributions)

    async def test_optional_metrics_use_unavailable_but_valid_zero_stays_zero(self):
        await self.stats.get_stats()
        self.stats.queries.query_rest = AsyncMock(return_value=None)
        self.assertIsNone(await self.stats.lines_changed)
        self.assertIsNone(await self.stats.views)
        self.stats.queries.query_rest.side_effect = [[], {"views": []}]
        self.assertEqual(await self.stats.lines_changed, (0, 0))
        self.assertEqual(await self.stats.views, 0)


class PublicationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "stats.toml"
        self.config.write_text('[stats]\nuser = "rico"\n')
        self.output = self.root / "generated"
        self.output.mkdir()
        self.old = {
            "languages.svg": "previous languages",
            "overview.svg": "previous overview",
            "stats.json": "previous snapshot",
        }
        for name, content in self.old.items():
            (self.output / name).write_text(content)
        self.env = patch.dict(
            os.environ,
            {
                "ACCESS_TOKEN": "test-token",
                "EXCLUDED": "",
                "EXCLUDED_LANGS": "",
            },
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    async def query(self, query):
        if "repositories(" in query:
            return page([repository()])
        if "contributionYears" in query:
            return {
                "data": {
                    "viewer": {"contributionsCollection": {"contributionYears": [2026]}}
                }
            }
        return {
            "data": {
                "viewer": {
                    "year2026": {"contributionCalendar": {"totalContributions": 653}}
                }
            }
        }

    def assert_previous_outputs(self):
        for name, content in self.old.items():
            self.assertEqual((self.output / name).read_text(), content)

    async def test_api_failure_preserves_all_previous_outputs(self):
        with patch.object(
            Queries, "query", AsyncMock(side_effect=GitHubAPIError("failed"))
        ):
            with self.assertRaises(GitHubAPIError):
                await generate_images.main(self.config, self.output)
        self.assert_previous_outputs()

    async def test_late_metric_failure_preserves_all_outputs(self):
        with (
            patch.object(Queries, "query", side_effect=self.query),
            patch.object(
                Queries, "query_rest", AsyncMock(side_effect=GitHubAPIError("failed"))
            ),
        ):
            with self.assertRaises(GitHubAPIError):
                await generate_images.main(self.config, self.output)
        self.assert_previous_outputs()

    async def test_malformed_svg_cannot_replace_published_outputs(self):
        with (
            patch.object(Queries, "query", side_effect=self.query),
            patch.object(Queries, "query_rest", AsyncMock(return_value=None)),
            patch.object(render_cards, "languages", return_value="not XML"),
        ):
            with self.assertRaises(generate_images.ElementTree.ParseError):
                await generate_images.main(self.config, self.output)
        self.assert_previous_outputs()

    async def test_valid_data_updates_all_outputs_and_escapes_display_name(self):
        with (
            patch.object(Queries, "query", side_effect=self.query),
            patch.object(Queries, "query_rest", AsyncMock(return_value=None)),
        ):
            await generate_images.main(self.config, self.output)
        overview = (self.output / "overview.svg").read_text()
        languages = (self.output / "languages.svg").read_text()
        self.assertIn("Rico &amp; friends", overview)
        self.assertIn("653", overview)
        self.assertIn("N/A", overview)
        self.assertIn("60.00%", languages)
        self.assertIn("40.00%", languages)
        snapshot = Snapshot.load(self.output / "stats.json")
        self.assertEqual(snapshot.user, "rico")
        self.assertEqual(snapshot.repositories, 1)
        self.assertEqual(sum(lang.size for lang in snapshot.languages), 100)
        self.assertNotIn("rico/project", snapshot.to_json())
        self.assertNotIn("test-token", snapshot.to_json())

    async def test_offline_rendering_needs_no_token_and_keeps_original_timestamp(self):
        with (
            patch.object(Queries, "query", side_effect=self.query),
            patch.object(Queries, "query_rest", AsyncMock(return_value=None)),
        ):
            await generate_images.main(self.config, self.output)
        old_contents = {name: (self.output / name).read_text() for name in self.old}
        with (
            patch.dict(os.environ, {"ACCESS_TOKEN": ""}),
            patch.object(
                generate_images,
                "collect",
                AsyncMock(side_effect=AssertionError("Network access")),
            ),
        ):
            await generate_images.main(
                self.root / "does-not-exist.toml",
                self.output,
                self.output / "stats.json",
            )
        for name, contents in old_contents.items():
            self.assertEqual((self.output / name).read_text(), contents)

    async def test_staging_write_failure_preserves_all_published_outputs(self):
        original_write = Path.write_text

        def fail_snapshot_write(path, *args, **kwargs):
            if path.name == "stats.json" and path.parent.name.startswith(".stats-"):
                raise OSError("Disk write failed")
            return original_write(path, *args, **kwargs)

        with (
            patch.object(Queries, "query", side_effect=self.query),
            patch.object(Queries, "query_rest", AsyncMock(return_value=None)),
            patch.object(Path, "write_text", fail_snapshot_write),
        ):
            with self.assertRaises(OSError):
                await generate_images.main(self.config, self.output)
        self.assert_previous_outputs()

    async def test_disabled_optional_metrics_do_not_make_rest_requests(self):
        self.config.write_text(
            '[stats]\nuser = "rico"\ncollect_lines_changed = false\ncollect_views = false\n'
        )
        with (
            patch.object(Queries, "query", side_effect=self.query),
            patch.object(Queries, "query_rest", AsyncMock()) as request,
        ):
            await generate_images.main(self.config, self.output)
        request.assert_not_awaited()
        snapshot = Snapshot.load(self.output / "stats.json")
        self.assertIsNone(snapshot.lines_changed)
        self.assertIsNone(snapshot.views_14d)


if __name__ == "__main__":
    unittest.main()
