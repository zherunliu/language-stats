import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import render_cards
from settings import Settings
from snapshot import SCOPE, Snapshot


def example():
    return {
        "schema_version": 1,
        "scope": dict(SCOPE),
        "user": "rico",
        "name": "Rico & friends",
        "collected_at": "2026-09-29T09:00:00+00:00",
        "repositories": 2,
        "stars": 3,
        "forks": 1,
        "contributions": 653,
        "lines_changed": None,
        "views_14d": 0,
        "languages": [
            {"name": "Python", "size": 60, "color": "#3572A5"},
            {"name": "TypeScript", "size": 40, "color": "#3178c6"},
        ],
    }


class SnapshotTests(unittest.TestCase):
    def test_rejects_unknown_scope_missing_fields_and_invalid_counts(self):
        for key, value in (
            ("schema_version", 2),
            ("schema_version", True),
            ("scope", {}),
            ("user", "invalid/user"),
            ("name", ""),
            ("collected_at", "2026-09-29"),
            ("repositories", 0),
            ("stars", True),
            ("contributions", -1),
            ("lines_changed", "unknown"),
            ("languages", []),
        ):
            with self.subTest(key=key):
                data = example()
                data[key] = value
                with self.assertRaises(ValueError):
                    Snapshot.from_dict(data)

    def test_rejects_negative_zero_or_duplicate_language_data(self):
        for languages in (
            [{"name": "Python", "size": -1}],
            [{"name": "Python", "size": 0}],
            [{"name": "Python", "size": 60}, {"name": "python", "size": 40}],
        ):
            data = example()
            data["languages"] = languages
            with self.assertRaises(ValueError):
                Snapshot.from_dict(data)

    def test_missing_optional_metric_fields_are_not_implicitly_unavailable(self):
        for field in ("lines_changed", "views_14d"):
            data = example()
            del data[field]
            with self.assertRaisesRegex(ValueError, "missing"):
                Snapshot.from_dict(data)

    def test_zero_and_unavailable_remain_distinct_in_json(self):
        snapshot = Snapshot.from_dict(example())
        data = json.loads(snapshot.to_json())
        self.assertIsNone(data["lines_changed"])
        self.assertEqual(data["views_14d"], 0)
        self.assertEqual(Snapshot.from_dict(data), snapshot)

    def test_svg_sanitizes_colors_and_escapes_untrusted_names(self):
        data = example()
        data["name"] = '<script>alert("x")</script>'
        data["languages"][0]["name"] = "C<&>"
        data["languages"][0]["color"] = '"/><script>bad</script>'
        snapshot = Snapshot.from_dict(data)
        for svg in (render_cards.overview(snapshot), render_cards.languages(snapshot)):
            root = ElementTree.fromstring(svg)
            self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
            self.assertFalse(root.findall(".//{http://www.w3.org/2000/svg}script"))
            self.assertNotIn("foreignObject", svg)
        self.assertIn("C&lt;&amp;&gt;", render_cards.languages(snapshot))
        self.assertIn('fill="#8b949e"', render_cards.languages(snapshot))

    def test_cards_stay_fixed_size_and_show_only_top_six_languages(self):
        data = example()
        for count in (2, 6, 12, 25):
            with self.subTest(count=count):
                data["languages"] = [
                    {"name": f"Language{i}", "size": count - i, "color": "#123456"}
                    for i in range(count)
                ]
                snapshot = Snapshot.from_dict(data)
                overview = ElementTree.fromstring(render_cards.overview(snapshot))
                languages = ElementTree.fromstring(render_cards.languages(snapshot))
                for attribute in ("width", "height", "viewBox"):
                    self.assertEqual(overview.attrib[attribute], languages.attrib[attribute])
                self.assertEqual(languages.attrib["height"], "286")
                labels = [
                    element.text
                    for element in languages.iter("{http://www.w3.org/2000/svg}text")
                ]
                for i in range(count):
                    if i < render_cards.LANGUAGE_LIMIT:
                        self.assertIn(f"Language{i}", labels)
                    else:
                        self.assertNotIn(f"Language{i}", labels)
                total = count * (count + 1) // 2
                self.assertIn(f"{count / total:.2%}", labels)

    def test_percentages_use_current_bytes_instead_of_cached_proportions(self):
        data = copy.deepcopy(example())
        data["languages"][0]["prop"] = 1
        svg = render_cards.languages(Snapshot.from_dict(data))
        self.assertIn("60.00%", svg)
        self.assertIn("40.00%", svg)


class SettingsTests(unittest.TestCase):
    def load(self, source):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stats.toml"
            path.write_text(source)
            return Settings.load(path)

    def test_file_filters_and_existing_secret_filters_are_merged(self):
        with patch.dict(
            os.environ,
            {
                "EXCLUDED": " rico/Private, ",
                "EXCLUDED_LANGS": " TeX,",
            },
        ):
            settings = self.load(
                '[stats]\nuser="rico"\nexclude_repos=["Rico/Example"]\nexclude_languages=["HTML"]'
            )
        self.assertEqual(settings.exclude_repos, {"rico/private", "rico/example"})
        self.assertEqual(settings.exclude_languages, {"tex", "html"})

    def test_unknown_setting_and_non_boolean_flags_are_rejected(self):
        for option in (
            "collect_view=true",
            'collect_views="false"',
            'exclude_repos="repo"',
            'user="a/b"',
        ):
            with self.subTest(option=option):
                source = (
                    "[stats]\n"
                    + ("" if option.startswith("user=") else 'user="rico"\n')
                    + option
                )
                with self.assertRaises(ValueError):
                    self.load(source)


if __name__ == "__main__":
    unittest.main()
