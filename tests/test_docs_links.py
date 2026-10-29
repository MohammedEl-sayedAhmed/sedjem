"""Tests for the links between the docs: every relative link in README.md and the markdown files under
docs/ must reach a file, and a link to a heading (#anchor) must reach a heading of that file, as GitHub
names its anchors. Offline, standard library only.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
LINK = re.compile(r"\]\(([^)\s]+)\)")


def slug(heading):
    """GitHub's anchor for a heading: lower case, code and link markup dropped, every character but
    letters, digits, spaces, hyphens and underscores removed, spaces turned into hyphens."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading.replace("`", ""))
    return "".join(c for c in text.strip().lower() if c.isalnum() or c in " -_").replace(" ", "-")


def anchors(path):
    """The anchors of a markdown file: its headings (a repeated one gets -1, -2 and so on) and any
    <a id> or <a name>."""
    found, seen, fenced = set(), {}, False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if fenced:
            continue
        heading = re.match(r"#{1,6} (.*)", line)
        if heading:
            s = slug(heading.group(1))
            found.add(f"{s}-{seen[s]}" if s in seen else s)
            seen[s] = seen.get(s, 0) + 1
        found.update(re.findall(r'<a (?:id|name)="([^"]+)"', line))
    return found


class DocsLinksTest(unittest.TestCase):
    def test_slug_follows_github(self):
        self.assertEqual(slug("Public test sets: ArzEn and AMI"), "public-test-sets-arzen-and-ami")
        self.assertEqual(slug("Forced alignment (experimental)"), "forced-alignment-experimental")
        self.assertEqual(slug("NVIDIA Nemotron-3-Diarization"), "nvidia-nemotron-3-diarization")
        self.assertEqual(slug("The `--align` option"), "the---align-option")

    def test_relative_links_reach_a_file_and_a_heading(self):
        checked = 0
        for doc in DOCS:
            text = re.sub(r"```.*?```", "", doc.read_text(encoding="utf-8"), flags=re.S)
            for target in LINK.findall(text):
                if re.match(r"[a-z][a-z0-9+.-]*:", target):  # https:, mailto: and the like
                    continue
                path, _, anchor = target.partition("#")
                dest = (doc.parent / path).resolve() if path else doc
                with self.subTest(doc.relative_to(ROOT).as_posix(), link=target):
                    self.assertTrue(dest.exists(), "no such file")
                    if anchor and dest.suffix == ".md":
                        self.assertIn(anchor, anchors(dest), "no such heading")
                checked += 1
        self.assertGreater(checked, 100)


if __name__ == "__main__":
    unittest.main()
