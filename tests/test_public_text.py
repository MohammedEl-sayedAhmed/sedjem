"""Tests for the repository's rules for public text (docs/14-pipeline.md). The repository is public, so no file in
git names the private meetings or the tools they came from, and none has a path into someone's own home folder
(examples use made-up users such as /home/you). The docs read as plain prose: no bold and no em dashes. The quick
checks of the Tests workflow run these. Offline, standard library only.
Run: .venv/bin/python -m unittest tests.test_public_text -v
"""
import hashlib
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The private names, as the SHA-256 of each in lower case, so that this file doesn't name them either. A word in
# any file, in any letter case and also inside a longer word written in capitals (FooBar has foo and bar), counts.
# To add one: python3 -c "import hashlib; print(hashlib.sha256(b'name').hexdigest())"
PRIVATE = {
    "6d7b84b189facf7a230818a947b4777c19df801fc017f47db74bf37f7502948e",
    "cbbdbec3d2fe31abb19e00e2fca057410dec91f4e30a1ac0fe6f4eafbae84266",
    "c0d67255424148d869b8c00b4e7c27645b97147e36aaf9538a661a3d87883098",
    "fb7deb7c3c6d370d95a95994c507b2d09f5acaf2a9c56b7614569745242abcb7",
}
# A home folder on Linux, macOS or Windows (with its backslashes doubled too, as in JSON), and the made-up users
# that examples and tests may put there
HOME = re.compile(r"(?:/home/|/Users/|\b[A-Za-z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/))([A-Za-z0-9._-]+)")
PLACEHOLDERS = {"you", "alice", "bob", "user", "runner", "name", "me"}
# Raw research material, kept as it was written (docs/README.md)
NOT_DOCS = ("docs/research/",)


def tracked():
    """The files in git and the new ones git would add (not those it ignores), or None outside a checkout."""
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [name for name in out.decode("utf-8").split("\0") if name]


def text(name):
    """A file's text, or None for a binary file, one that isn't UTF-8, or one deleted but not yet out of git."""
    try:
        data = (ROOT / name).read_bytes()
    except OSError:
        return None
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def words(line):
    """The words of a line in lower case: each run of letters, and the parts of one written in capitals."""
    runs = re.findall(r"[A-Za-z]+", line)
    parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", line)
    return {w.lower() for w in runs + parts}


def private(word):
    return hashlib.sha256(word.encode()).hexdigest() in PRIVATE


def prose(markdown):
    """Markdown without its code: fenced blocks and `code spans`, where ** or a dash can stand for themselves."""
    out, fenced = [], False
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            out.append("")
            continue
        out.append("" if fenced else re.sub(r"`+[^`]*`+", "", line))
    return out


def docs(files):
    """The docs the plain style applies to: every Markdown file but the research material, and the issue forms."""
    return [f for f in files if (f.endswith(".md") and not f.startswith(NOT_DOCS))
            or (f.startswith(".github/ISSUE_TEMPLATE/") and f.endswith(".yml"))]


class Rules(unittest.TestCase):
    def test_words_and_their_hashes(self):
        self.assertEqual(words("FooBar foo_bar HTTPServer x2y"), {"foobar", "foo", "bar", "httpserver", "http",
                                                                 "server", "x", "y"})
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", h) for h in PRIVATE) and len(PRIVATE) == 4)
        self.assertFalse(any(private(w) for w in words("the SprintPlanning notes")))
        PRIVATE.add(hashlib.sha256(b"sprint").hexdigest())
        try:
            self.assertTrue(any(private(w) for w in words("the SprintPlanning notes")))
        finally:
            PRIVATE.discard(hashlib.sha256(b"sprint").hexdigest())

    def test_home_folders(self):
        found = lambda line: [m.group(1) for m in HOME.finditer(line)]
        self.assertEqual(found('cd "/home/you/sedjem"; /Users/bob/x; C:\\Users\\alice\\y; "C:\\\\Users\\\\me"'),
                         ["you", "bob", "alice", "me"])
        self.assertEqual(found("~/.local/share/sedjem and %LOCALAPPDATA%\\Sedjem"), [])

    def test_prose_leaves_out_code(self):
        self.assertEqual(prose("a `**/*.py` b\n```sh\nls **\n```\nc"), ["a  b", "", "", "", "c"])


class PublicText(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = tracked()
        if cls.files is None:
            raise unittest.SkipTest("not a git checkout")

    def test_no_private_names(self):
        found = []
        for name in self.files:
            body = text(name)
            if body is None or not any(private(w) for w in words(body)):
                continue
            for n, line in enumerate(body.splitlines(), 1):
                if any(private(w) for w in words(line)):
                    found.append(f"{name}:{n}")
        self.assertEqual(found, [], "a private name (one of the hashes in tests/test_public_text.py)")
        self.assertGreater(len(self.files), 300)

    def test_no_home_folders(self):
        found = []
        for name in self.files:
            body = text(name)
            for n, line in enumerate((body or "").splitlines(), 1):
                found += [f"{name}:{n}: {m.group(0)}" for m in HOME.finditer(line) if m.group(1) not in PLACEHOLDERS]
        self.assertEqual(found, [], "a path into someone's home folder; examples use a made-up user such as /home/you")

    def test_docs_without_bold_or_em_dashes(self):
        files = docs(self.files)
        self.assertIn("README.md", files)
        self.assertIn("docs/privacy.md", files)
        self.assertIn(".github/release-notes/v0.3.1.md", files)
        self.assertFalse([f for f in files if f.startswith("docs/research/")])
        found = []
        for name in files:
            for n, line in enumerate(prose(text(name) or ""), 1):
                if "**" in line:
                    found.append(f"{name}:{n}: bold")
                if "\u2014" in line:
                    found.append(f"{name}:{n}: an em dash")
        self.assertEqual(found, [], "plain prose: commas, colons, parentheses or a new sentence instead")


if __name__ == "__main__":
    unittest.main()
