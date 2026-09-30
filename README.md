# language-stats

A personal GitHub statistics tool that collects data monthly and generates two SVG cards for a Profile README: an overview and a language breakdown. Built with Python and uv; no server deployment is required.

<table>
<tr>
<td><picture>
  <source media="(prefers-color-scheme: dark)" srcset="generated/overview.svg#gh-dark-mode-only">
  <img alt="GitHub statistics" src="generated/overview.svg#gh-light-mode-only" width="420">
</picture></td>
<td><picture>
  <source media="(prefers-color-scheme: dark)" srcset="generated/languages.svg#gh-dark-mode-only">
  <img alt="Repository languages" src="generated/languages.svg#gh-light-mode-only" width="420">
</picture></td>
</tr>
</table>

Before the first successful collection, uncollected metrics display `N/A`. A successful run creates `generated/stats.json` and replaces the cards with validated results.

## Statistics scope

| Metric | Scope |
| --- | --- |
| Repositories, stars, and forks | Non-fork repositories owned by `stats.user`, visible to the token, and not excluded |
| Language percentages | Sum of code bytes per language across those repositories, recalculated after language exclusions |
| All-time contributions | Sum of yearly GitHub contribution calendar totals, independent of repository filters; this is not a commit count |
| Lines changed | Your additions + deletions reported by GitHub's contributor statistics API for the selected repositories |
| Repository views | Views for the selected repositories over the last 14 days; monthly collection does not produce a full-month total |

Language percentages describe repository code size, not personally authored code or proficiency. Private repositories are included when visible to the token. Outputs contain aggregate values without private repository names, repository lists, or tokens. Publishing these outputs also makes their private-data aggregates public.

Lines changed and views are supplementary metrics. GitHub's APIs have caching, permission, and data limitations, so these values should not be treated as precise measures of work. Contributor statistics exclude merge and empty commits, and large repositories may return zero line counts; see the [GitHub statistics API documentation](https://docs.github.com/en/rest/metrics/statistics). Views cover only the last 14 days; see the [GitHub traffic API documentation](https://docs.github.com/en/rest/metrics/traffic).

## Configuration and local use

Settings are stored in [`stats.toml`](stats.toml). The default user is `zherunliu`. Only owned, non-fork repositories are collected; external contribution repositories are not queried.

```toml
[stats]
user = "zherunliu"
exclude_repos = []           # Full owner/repo names; exact, case-insensitive matches
exclude_languages = []       # For example ["HTML", "TeX"]; case-insensitive
collect_lines_changed = true
collect_views = true
```

Set a supplementary metric's flag to `false` to skip its API requests and display `N/A`. Optional `EXCLUDED` and `EXCLUDED_LANGS` environment variables or Actions secrets accept comma-separated values, which are merged with file settings. Keep exclusion entries containing private repository names in secrets.

Credentials are read only from the `ACCESS_TOKEN` environment variable. The token must belong to `stats.user`. A repository's default `GITHUB_TOKEN` cannot replace the personal token needed to read data across your repositories. A classic personal access token needs `read:user` and `repo` scopes to read private repositories.

For a fine-grained token, select `zherunliu` (or the configured user) as the resource owner and include the repositories you intend to collect. Repository and account permissions both apply to GraphQL fields. The contributor statistics REST endpoint requires Metadata read access; views require Administration read access and display `N/A` when unavailable. See [GraphQL authentication](https://docs.github.com/en/graphql/guides/forming-calls-with-graphql) and [traffic permissions](https://docs.github.com/en/rest/metrics/traffic#get-page-views). The personal token is used for collection; publishing uses the workflow's default Actions credentials.

With uv installed:

```sh
uv sync --locked
uv run --frozen python -m unittest discover -s tests -v
# Securely supply ACCESS_TOKEN in the environment before running:
uv run --frozen python generate_images.py
```

The Python version is selected by `.python-version`; dependencies are managed by `pyproject.toml` and `uv.lock`. The script finds its project configuration regardless of the current working directory. You can also specify `--config` and `--output-dir`.

Generated files:

- `generated/overview.svg`: summary metrics with labels identifying their scope.
- `generated/languages.svg`: the six largest languages by repository code bytes, with percentages based on all included languages. The color bar still represents all included languages. Both cards stay at 420 × 286 pixels.
- `generated/stats.json`: an aggregate snapshot from the same collection, recording the UTC timestamp, statistics scope, language byte counts, and availability of supplementary metrics.

Use the saved JSON to inspect values or redraw cards without a token or network access. Offline rendering preserves the original collection timestamp:

```sh
uv run --frozen python generate_images.py --from-json generated/stats.json --output-dir /tmp/language-stats-preview
```

## Monthly updates

The [`Update Personal Statistics`](.github/workflows/main.yml) workflow runs on the first day of each month at 00:23 UTC (08:23 China time). It also supports manual runs from the Actions page. Changes to statistics code or configuration pushed to `main` trigger a run; output-only or README-only changes do not.

Configure `ACCESS_TOKEN` in the repository's Actions secrets. The workflow uses the default Actions credentials to publish generated files. The workflow installs locked dependencies with uv, runs regression tests, collects data, and commits only the three generated files. Workflow runs are serialized.

HTTP/GraphQL errors, missing fields, invalid pagination, or empty repository/language results fail the run. Collection, rendering, and validation finish before output files are written; collection or rendering failures preserve previous results. Files are replaced individually, so an interrupted replacement is not a transaction across all files. Actions commits the outputs only after the entire generation step succeeds.

Supplementary metrics display `N/A` when inaccessible, denied (403/404), or still pending after a bounded wait, while valid zero values display `0`. Rate limits, network failures, and malformed responses fail the run. GraphQL errors identify known query field paths with list indices masked, when GitHub supplies a path. Logs do not print raw API error bodies, arbitrary path segments, or private repository URLs. Resolve the cause, such as token permissions, and rerun manually. Each card's timestamp makes older results identifiable.

## Profile README images

Use this markup in the Profile README to show two equal-sized cards side by side. GitHub selects each card's light or dark colors from the visitor's theme:

```html
<table>
<tr>
<td><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/zherunliu/language-stats/main/generated/overview.svg#gh-dark-mode-only">
  <img alt="GitHub statistics" src="https://raw.githubusercontent.com/zherunliu/language-stats/main/generated/overview.svg#gh-light-mode-only" width="420">
</picture></td>
<td><picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/zherunliu/language-stats/main/generated/languages.svg#gh-dark-mode-only">
  <img alt="Repository languages" src="https://raw.githubusercontent.com/zherunliu/language-stats/main/generated/languages.svg#gh-light-mode-only" width="420">
</picture></td>
</tr>
</table>
```

`render_cards.py` generates native SVG cards with light/dark theme support.

## Attribution and license

Originally created from [jstrieb/github-stats](https://github.com/jstrieb/github-stats). The API collector is adapted from that implementation; configuration, snapshots, and SVG rendering are maintained for this project's needs. The original [GPL-3.0 license](LICENSE) and attribution are retained.
