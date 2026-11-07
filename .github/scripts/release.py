"""Releases of Sedjem: the checks before one, its tag, and its notes. The Release workflow
(.github/workflows/release.yml) runs it, and the Desktop build workflow takes a release's notes from it; the app
doesn't. docs/14-pipeline.md describes the whole process.

  check  whether VERSION may be released from a commit: it is X.Y.Z, the version in app/__init__.py, newer than
         every release and without a release of its own; its tag is free or already on that commit; the commit
         is on main, and its Tests run for the push to main passed (on Windows and Linux). Gives commit, tag,
         previous (the newest release) and tagged (whether the tag is there already) as step outputs.
  tag    tags the commit vX.Y.Z, a lightweight tag like those of the earlier releases. A tag already on that
         commit is kept (a release run again after a failed build); a tag on another commit stops it.
  notes  the release notes, .github/release-notes/vX.Y.Z.md. A file that is there is used as it is. Otherwise
         one is drafted from the titles of the commits since the previous release, grouped by the area each
         title starts with ("Updates: ...", "Website: ..."), between the download section and the licence of
         the hand-written notes; tests, docs and the pipeline come last.

check and tag talk to GitHub's REST API with GITHUB_TOKEN: check reads (contents and actions), tag needs
contents: write. notes needs only git, with the tags and the history (fetch-depth: 0 in a workflow). Only the
standard library is needed; the versions and the API calls are those of preview_plan.py, beside it. To see what
it would do:
    GITHUB_TOKEN=$(gh auth token) python3 .github/scripts/release.py check 0.3.2 --commit SHA --repo OWNER/NAME
    GITHUB_TOKEN=$(gh auth token) python3 .github/scripts/release.py tag 0.3.2 --commit SHA --repo OWNER/NAME --dry-run
    python3 .github/scripts/release.py notes 0.3.2 --dry-run
Without --dry-run, notes writes the draft into .github/release-notes/, to be edited and committed before the
release.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preview_plan as pp  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NOTES = Path(".github") / "release-notes"
REPO = "MohammedEl-sayedAhmed/sedjem"  # for the links of notes drafted outside a workflow
AREA = re.compile(r"([^:]{1,60}):\s+(\S.*)")
RELEASE_COMMIT = re.compile(r"(Sedjem|Version) \d+\.\d+\.\d+")  # "Sedjem 0.3.1: the version, and the release notes"
# areas whose changes don't reach the app itself: listed last, with their area
UPKEEP = {"tests", "test", "docs", "readme", "stats", "website", "workflows", "pipeline", "dependencies",
          "desktop build", "release notes"}
UPDATES_FROM = (0, 3, 0)  # the first version that updates itself


# ---- check and tag -------------------------------------------------------------------------------

def app_version(root=ROOT):
    """__version__ in app/__init__.py, read without importing the app."""
    text = (root / "app" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__ = "([^"]+)"', text, re.M).group(1)


def tagged_commit(repo, api, tag):
    """The commit a tag points at (through an annotated tag too), or None when there is no such tag."""
    try:
        obj = api(f"/repos/{repo}/git/ref/tags/{pp.ref(tag)}")["object"]
    except pp.GitHubError as e:
        if e.status == 404:
            return None
        raise
    if obj["type"] == "tag":
        obj = api(f"/repos/{repo}/git/tags/{obj['sha']}")["object"]
    return obj["sha"]


def check(version, commit, app, repo, api):
    """{"tag", "previous", "tagged"} for a release of `version` from `commit`, where `app` is the version in
    app/__init__.py at that commit. ValueError says why there can't be one."""
    if not pp.RELEASE.fullmatch(version):
        raise ValueError(f"{version} is not a release version (X.Y.Z)")
    if not pp.SHA.fullmatch(commit or ""):
        raise ValueError(f"{commit} is not the full sha of a commit")
    if version != app:
        raise ValueError(f"{version} doesn't match the version in app/__init__.py at {commit[:7]} ({app}); "
                         "change it there first")
    tag = f"v{version}"
    releases = pp.list_releases(repo, api)
    if any(r.get("tag_name") == tag for r in releases):
        raise ValueError(f"{tag} has a release already")
    previous = pp.newest([r for r in releases if pp.is_release(r)])
    if previous and pp.order(version) <= pp.order(pp.tag_version(previous["tag_name"])):
        raise ValueError(f"{version} is not newer than {previous['tag_name']}, the newest release")
    there = tagged_commit(repo, api, tag)
    if there not in (None, commit):
        raise ValueError(f"the tag {tag} is on {there[:7]}, not on {commit[:7]}")
    if pp.compare(repo, api, commit, pp.BRANCH) not in ("ahead", "identical"):
        raise ValueError(f"{commit[:7]} is not on {pp.BRANCH}")
    runs = api(f"/repos/{repo}/actions/workflows/{pp.TESTS}/runs?head_sha={commit}&event=push&branch={pp.BRANCH}"
               "&per_page=20")
    if not any(pp.tested(r, repo) and r["head_sha"] == commit for r in runs.get("workflow_runs", [])):
        raise ValueError(f"the Tests workflow hasn't passed for {commit[:7]} on {pp.BRANCH}: wait for it, or run "
                         "it again if it failed for a reason that has nothing to do with the commit")
    return {"tag": tag, "previous": previous["tag_name"] if previous else "", "tagged": there == commit}


def tag(version, commit, repo, api, dry_run=False):
    """Tags `commit` v<version>; returns what was done. ValueError when the tag is on another commit."""
    name = f"v{version}"
    there = tagged_commit(repo, api, name)
    if there == commit:
        return f"{name} is on {commit[:7]} already"
    if there:
        raise ValueError(f"the tag {name} is on {there[:7]}, not on {commit[:7]}")
    if dry_run:
        return f"{commit[:7]} would be tagged {name}"
    api(f"/repos/{repo}/git/refs", "POST", {"ref": f"refs/tags/{name}", "sha": commit})
    return f"{commit[:7]} is tagged {name}"


# ---- notes ---------------------------------------------------------------------------------------

def git(*args, cwd=ROOT):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          encoding="utf-8").stdout


def previous_release(version, commit="HEAD", cwd=ROOT):
    """The newest release tag older than `version` among the tags `commit` contains, or None."""
    tags = git("tag", "--merged", commit, "--list", "v*", cwd=cwd).split()
    older = [t for t in tags if pp.RELEASE.fullmatch(t[1:]) and pp.order(t[1:]) < pp.order(version)]
    return max(older, key=lambda t: pp.order(t[1:]), default=None)


def titles(since, commit="HEAD", cwd=ROOT):
    """The titles of the commits after `since` (a tag, or None for all) up to `commit`, oldest first, merges left
    out."""
    span = f"{since}..{commit}" if since else commit
    return [t for t in git("log", "--no-merges", "--reverse", "--format=%s", span, cwd=cwd).splitlines() if t.strip()]


def sentence(text):
    """A title as a sentence: a capital first letter (unless its first word has capitals of its own, as in macOS,
    or is a name such as app.py) and a full stop."""
    text = text.strip()
    first = text.split()[0]
    if first.islower() and first[0].isalpha() and not any(c in first for c in "/._"):
        text = text[0].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def upkeep(area):
    a = area.lower()
    return a in UPKEEP or a.endswith(" tests") or a.startswith("readme")


def draft(version, previous, commit_titles, repo=REPO):
    """The Markdown of a release's notes, drafted from the titles of its commits."""
    since = pp.tag_version(previous) if previous else None
    changes, last = {}, []
    for title in commit_titles:
        m = AREA.fullmatch(title.strip())
        if not m:
            changes.setdefault("Other changes", []).append(sentence(title))
        elif RELEASE_COMMIT.fullmatch(m.group(1)):
            continue
        elif upkeep(m.group(1)):
            last.append(f"{m.group(1)}: {m.group(2)}")
        else:
            changes.setdefault(m.group(1), []).append(sentence(m.group(2)))
    url = f"https://github.com/{repo}"
    lines = [f"Sedjem {version}" + (f", with the changes since {since}." if since else "."), "",
             "## Download", "",
             f"- Windows 10 and 11: `Sedjem-{version}-setup.exe` installs for your user, without admin rights. It isn't",
             "  signed yet, so SmartScreen warns about an unknown publisher: choose *More info*, then *Run anyway*."]
    if since:
        lines.append(f"  Installing over {since} keeps your transcriptions, keys and models.")
    lines += [f"- Linux (x64): unpack `Sedjem-{version}-linux-x64.tar.gz` and run `Sedjem/Sedjem`.",
              "- `SHA256SUMS.txt` has the checksums of both.", ""]
    if since and pp.order(since)[:3] >= UPDATES_FROM:
        lines += [f"{since} can update itself: *Settings → Updates*, then *Update now*.", ""]
    lines += ["## Changes", ""]
    if not changes and not last:
        lines += ["Nothing has changed since the previous release.", ""]
    other = changes.pop("Other changes", None)
    for area, items in [*sorted(changes.items(), key=lambda kv: kv[0].lower()), *([("Other changes", other)] if other else [])]:
        lines += [f"### {area}", "", *(f"- {item}" for item in items), ""]
    if last:
        lines += ["### Tests, docs and the pipeline", "", *(f"- {sentence(item)}" for item in last), ""]
    if previous:
        lines += [f"Every commit: [{previous}...v{version}]({url}/compare/{previous}...v{version}).", ""]
    lines += ["Sedjem is by Mohammed El-sayed Ahmed and is free software under the AGPL-3.0 (see LICENSE and NOTICE);",
              "commercial licences are available from the author.", ""]
    return "\n".join(lines)


def notes(version, commit="HEAD", repo=REPO, root=ROOT, dry_run=False):
    """(text, what was done): the notes of v<version>, from the file when it is there, else drafted and, unless
    dry_run, written into it."""
    path = root / NOTES / f"v{version}.md"
    if path.is_file():
        return path.read_text(encoding="utf-8"), f"the notes in {path.relative_to(root).as_posix()} are used as they are"
    previous = previous_release(version, commit, cwd=root)
    found = titles(previous, commit, cwd=root)
    text = draft(version, previous, found, repo)
    done = f"drafted from {len(found)} commits since {previous or 'the first one'}"
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        done += f" into {path.relative_to(root).as_posix()}"
    return text, done


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="whether a version may be released from a commit")
    p.add_argument("version")
    p.add_argument("--commit", required=True, help="its full sha")
    p.add_argument("--app-version", help="the version in app/__init__.py (default: read from this checkout)")
    p = sub.add_parser("tag", help="tag the commit vX.Y.Z")
    p.add_argument("version")
    p.add_argument("--commit", required=True, help="its full sha")
    p.add_argument("--dry-run", action="store_true", help="only say what would be tagged")
    p = sub.add_parser("notes", help="the release notes: the file when it is there, else a draft written into it")
    p.add_argument("version")
    p.add_argument("--commit", default="HEAD", help="the release's commit (default: HEAD)")
    p.add_argument("--dry-run", action="store_true", help="print the notes and write nothing")
    for p in sub.choices.values():
        p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="OWNER/NAME")
    args = ap.parse_args(argv)
    if args.command == "notes":
        if not pp.RELEASE.fullmatch(args.version):
            sys.exit(f"notes: {args.version} is not a release version (X.Y.Z)")
        text, done = notes(args.version, args.commit, args.repo or REPO, dry_run=args.dry_run)
        print(done, file=sys.stderr)
        if args.dry_run:
            sys.stdout.write(text)
        return
    if not args.repo:
        sys.exit("needs --repo (or GITHUB_REPOSITORY)")
    token = os.environ.get("GITHUB_TOKEN")
    api = lambda path, method="GET", body=None: pp.call(token, path, method, body)
    try:
        if args.command == "check":
            result = check(args.version, args.commit, args.app_version or app_version(), args.repo, api)
            pp.summary(f"Release {result['tag']} from {args.commit[:7]}: the checks passed "
                       f"(after {result['previous'] or 'no release'}; tag {'there already' if result['tagged'] else 'to be made'})")
            pp.output(commit=args.commit, **result)
        else:
            if not token and not args.dry_run:
                sys.exit("tag: needs GITHUB_TOKEN with contents: write")
            pp.summary(tag(args.version, args.commit, args.repo, api, args.dry_run))
    except ValueError as e:
        print(f"::error::{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
