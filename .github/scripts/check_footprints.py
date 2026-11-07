"""The pull-request side of the rule that keeps AI-tool attribution out of Sedjem's history (AGENTS.md, ADR 15).

Scans every commit in BASE..HEAD (its message and trailers, and the lines it adds), and the pull request's title and
description when PR_TITLE and PR_BODY are set, for the attribution that AI tools add: a co-author trailer naming an
assistant or an address of its maker, "Generated with" and "Created by" notes, "Powered by" lines, links to a
tool's chat or share pages, and the robot emoji. Using AI tools is fine; attributing them in the history is not.

Run by .github/workflows/footprints.yml on every pull request. The title and description come in through the
environment, never through the command line. The description of a pull request opened by a bot (PR_AUTHOR_TYPE=Bot)
is skipped: Dependabot quotes the release notes of other projects.

The files in ALLOWED name the patterns on purpose, so the lines they add are not scanned. Their commit messages are.
Invisible characters (zero-width spaces and joiners, the soft hyphen, the byte order mark) are removed before a scan,
and a no-break space counts as a space. A footprint written with look-alike letters is not found: the check is there
for a slip, not against someone who hides one.

  python3 .github/scripts/check_footprints.py BASE_SHA HEAD_SHA

Exit status: 0 when clean, 1 when a footprint is found, 2 when the commits can't be listed.
"""
import os
import re
import subprocess
import sys

# Files that name the patterns on purpose
ALLOWED = (
    ".github/scripts/check_footprints.py",
    "tests/test_check_footprints.py",
)

VERBS = ("created|written|authored|produced|made|built|implemented|developed|coded|drafted|crafted|forged|wrote|"
         "generated")
TOOLS = "claude|anthropic|openai|chatgpt|codex|gemini|copilot"
# (tag, pattern): matched per line, ignoring case
PATTERNS = (
    ("co-author", rf"co.?authored.?by[\s:].*\b(?:{TOOLS})\b"),
    ("assisted-by", rf"^\s*(?:assisted|reviewed|generated).?by[\s:].*\b(?:{TOOLS})\b"),
    ("maker-address", r"@(?:[a-z0-9-]+\.)*(?:anthropic|openai)\.com\b"),
    ("made-with", rf"\b(?:{VERBS})\s+(?:with|by|using|via)\s+\[?(?:{TOOLS})\b"),
    ("made-with-ai", rf"\b(?:{VERBS})\s+(?:with|by|using|via)\s+(?:an?\s+)?(?:ai|llm|ai.?assistant|ai.?tool)\b"),
    ("powered-by", rf"\bpowered\s+by\s+(?:{TOOLS})\b"),
    ("with-help-of", rf"\bwith\s+(?:the\s+)?(?:help|assistance)\s+of\s+(?:{TOOLS})\b"),
    ("tool-link", r"\bclaude\.ai/(?:code|chat|new|agent|share|workspace|p)\b|\bclaude\.com/(?:claude.?code|product|code|chat)\b"),
    ("robot-emoji", "\U0001F916|&#(?:129302|x1f916);|:robot_face:"),
)
COMPILED = tuple((tag, re.compile(pattern, re.I)) for tag, pattern in PATTERNS)
INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))


def clean(text):
    return text.translate(INVISIBLE).replace(" ", " ")


def scan(text):
    """(tag, line) for every line of the text that has a footprint."""
    return [(tag, line.strip()) for line in clean(text).splitlines() for tag, pattern in COMPILED
            if pattern.search(line)]


def added_lines(diff):
    """The lines a diff adds, outside the files in ALLOWED."""
    lines, skip = [], False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            skip = False
        elif line.startswith("+++ "):
            path = line[4:].strip().removeprefix("b/")
            skip = path in ALLOWED
        elif line.startswith("+") and not skip:
            lines.append(line[1:])
    return "\n".join(lines)


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=True).stdout


def report(where, found):
    for tag, line in found:
        print(f"::error::{where}: [{tag}] {line[:200]}".replace("\r", " ").replace("\n", " "))


def check(base, head, title="", body="", bot=False):
    """The number of places with a footprint among the commits of BASE..HEAD, the title and the description."""
    try:
        shas = git("rev-list", "--no-merges", "--reverse", f"{base}..{head}").split()
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"::error::The commits in {base}..{head} can't be listed: {e}")
        return None
    bad = 0
    for sha in shas:
        subject = git("log", "-1", "--format=%h %s", sha).strip()
        for where, text in ((f"commit {subject} (message)", git("log", "-1", "--format=%B", sha)),
                            (f"commit {subject} (added lines)", added_lines(git("show", "--format=", "-U0", "--no-color",
                                                                              "--diff-filter=AM", sha)))):
            found = scan(text)
            report(where, found)
            bad += bool(found)
    for where, text, skipped in (("the pull request title", title, False),
                                 ("the pull request description", body, bot)):
        found = [] if skipped else scan(text)
        report(where, found)
        bad += bool(found)
    return bad, len(shas)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        sys.exit(f"usage: {sys.argv[0]} BASE_SHA HEAD_SHA")
    done = check(*argv, title=os.environ.get("PR_TITLE", ""), body=os.environ.get("PR_BODY", ""),
                 bot=os.environ.get("PR_AUTHOR_TYPE") == "Bot")
    if done is None:
        return 2
    bad, count = done
    if bad:
        print("::error::This project keeps AI-tool attribution out of its history and its pull requests: no "
              "co-author trailers for an assistant, no 'generated with' notes, no robot emoji. Using AI tools is "
              "fine. Edit the title or description, or reword the commits and push again.")
        return 1
    print(f"No AI-tool attribution in {count} commit(s), the title or the description.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
