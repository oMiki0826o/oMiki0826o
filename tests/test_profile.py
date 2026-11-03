import importlib.util
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "update_profile.py"
spec = importlib.util.spec_from_file_location("profile", SCRIPT)
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def sample():
    return {
        "pinnedItems": {"nodes": [
            {"name": f"project-{i}", "nameWithOwner": f"oMiki0826o/project-{i}",
             "url": f"https://github.com/oMiki0826o/project-{i}", "description": f"Demo {i}"}
            for i in range(6)
        ]},
        "repositories": {"nodes": [
            {"name": "demo", "isFork": False, "isArchived": False,
             "stargazerCount": 10, "languages": {"edges": [
                 {"size": 200, "node": {"name": "Python", "color": "#3572A5"}},
                 {"size": 100, "node": {"name": "TypeScript", "color": "#3178C6"}},
             ]}},
            {"name": "fork", "isFork": True, "isArchived": False, "stargazerCount": 2,
             "languages": {"edges": [{"size": 500, "node": {"name": "Java", "color": "#b07219"}}]}},
        ]},
        "contributionsCollection": {"totalCommitContributions": 12},
        "pullRequests": {"totalCount": 3},
        "issues": {"totalCount": 4},
        "repositoriesContributedTo": {"totalCount": 2},
    }


class ReadmeTests(unittest.TestCase):
    def test_five_pins_from_github_order(self):
        rendered = p.pinned_table(sample())
        self.assertEqual(rendered.count("github.com/oMiki0826o/project-"), 5)
        self.assertNotIn("project-5", rendered)

    def test_descriptions_escape_table_and_html(self):
        data = sample()
        data["pinnedItems"]["nodes"][0]["description"] = "Hi | <script>bad</script>"
        text = p.pinned_table(data)
        self.assertIn("Hi \\| &lt;script&gt;bad&lt;/script&gt;", text)

    def test_malicious_repo_url_rejected(self):
        data = sample()
        data["pinnedItems"]["nodes"][0]["url"] = "https://example.com/unsafe"
        with self.assertRaises(ValueError):
            p.pinned_table(data)

    def test_missing_pins_does_not_replace_existing(self):
        with self.assertRaises(ValueError):
            p.pinned_table({"pinnedItems": {"nodes": []}})

    def test_marker_validation(self):
        with self.assertRaises(ValueError):
            p.replace_pins("no markers", "content")
        with self.assertRaises(ValueError):
            p.replace_pins(p.END + p.START, "content")

    def test_linguist_colors_and_exclude_forks(self):
        langs = p.language_totals(sample())
        self.assertEqual(langs, [("Python", 200, "#3572A5"), ("TypeScript", 100, "#3178C6")])
        svg = p.svg_languages(langs, dark=True)
        self.assertIn("#3572A5", svg)
        self.assertIn("#3178C6", svg)
        self.assertIn("66.7%", svg)
        ET.fromstring(svg)

    def test_dark_light_stats_are_valid_svg(self):
        for dark in (True, False):
            svg = p.svg_stats(sample(), 2026, dark=dark)
            ET.fromstring(svg)
            self.assertIn("Total Commits (2026):", svg)
            self.assertIn("12", svg)

    def test_atomic_prevalidation_no_changed_file_on_bad_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = f"hello {p.START}\nold\n{p.END} bye"
            (root / "README.md").write_text(original, encoding="utf-8")
            bad = sample()
            bad["repositories"]["nodes"] = []
            with self.assertRaises(ValueError):
                p.build(root, bad, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
            self.assertEqual((root / "README.md").read_text(encoding="utf-8"), original)
            self.assertFalse((root / "assets").exists())

    def test_real_readme_links_to_local_stats_and_snake(self):
        readme = (SCRIPT.parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn("./assets/top-langs-dark.svg", readme)
        self.assertIn("./assets/stats-light.svg", readme)
        self.assertIn("/output/snake.svg", readme)
        self.assertNotIn("github-readme-stats.vercel.app", readme)
        self.assertEqual(readme.count(p.START), 1)
        self.assertEqual(readme.count(p.END), 1)


if __name__ == "__main__":
    unittest.main()
