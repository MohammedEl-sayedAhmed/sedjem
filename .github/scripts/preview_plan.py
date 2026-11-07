"""Nightly preview builds of the desktop app: whether to make one, its version and release notes, and the
removal of old ones. The workflows run it; the app doesn't.

  plan    once a day (.github/workflows/preview.yml): main's newest commit whose Tests workflow passed on
          Windows and Linux is built, unless the newest release or the newest preview already contains it.
          Gives build=true|false, version, commit and why, as step outputs.
  check   before every desktop build (.github/workflows/desktop.yml): whether a version may be built and
          published. A release X.Y.Z must match app/__init__.py; a preview must be the next patch after the
          newest release. Gives channel (stable or preview) and base (the release a preview follows).
  notes   the text of a preview's pre-release.
  prune   after the nightly build: deletes preview pre-releases and their tags once they are a week old,
          always keeping the newest. Releases are never touched.

Versions: X.Y.Z for a release. X.Y.Z-preview.YYYYMMDD for a preview, where X.Y.Z is the next patch after the
newest release and the date is the day it was built (UTC); a second preview the same day gets .2, a third
.3, and so on. So 0.2.0 < 0.2.1-preview.20261021 < 0.2.1-preview.20261021.2 < 0.2.1-preview.20261022 < 0.2.1.
A preview's pre-release is tagged v<version> and targets the full sha of its commit.

Only the standard library is needed (the workflow fetches this file without cloning the repository). It
talks to GitHub's REST API with GITHUB_TOKEN: reading for plan and check, contents: write for prune. To see
what it would do:
    GITHUB_TOKEN=$(gh auth token) python3 .github/scripts/preview_plan.py plan --repo OWNER/NAME
    GITHUB_TOKEN=$(gh auth token) python3 .github/scripts/preview_plan.py prune --repo OWNER/NAME --dry-run
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.github.com"
RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
PREVIEW = re.compile(r"(\d+)\.(\d+)\.(\d+)-preview\.(\d{8})(?:\.([1-9]\d*))?")
SHA = re.compile(r"[0-9a-f]{40}")
KEEP_DAYS = 7
TESTS = "tests.yml"  # the Tests workflow: both systems, every push to main
BRANCH = "main"
SELF = ".github/scripts/preview_plan.py"


# ---- versions ------------------------------------------------------------------------------------

def order(version):
    """A sort key for a version, None for anything else. A preview sorts after the release before it and
    before its own: (X, Y, Z, 0, date, N) against (X, Y, Z, 1, 0, 0)."""
    m = RELEASE.fullmatch(version or "")
    if m:
        return (*map(int, m.groups()), 1, 0, 0)
    m = PREVIEW.fullmatch(version or "")
    if m:
        x, y, z, day, n = m.groups()
        return (int(x), int(y), int(z), 0, int(day), int(n or 1))
    return None


def tag_version(tag):
    """The version a tag names (v0.2.0 -> 0.2.0), or None."""
    return tag[1:] if isinstance(tag, str) and tag.startswith("v") and order(tag[1:]) else None


def next_patch(version):
    x, y, z = map(int, RELEASE.fullmatch(version).groups())
    return f"{x}.{y}.{z + 1}"


def is_release(r):
    """A published release (not a draft or a pre-release) of a version X.Y.Z."""
    return not r.get("draft") and not r.get("prerelease") and bool(RELEASE.fullmatch(tag_version(r.get("tag_name")) or ""))


def is_preview(r):
    """A preview pre-release (a draft too: a publication that failed half-way)."""
    return bool(r.get("prerelease")) and bool(PREVIEW.fullmatch(tag_version(r.get("tag_name")) or ""))


def newest(releases):
    return max(releases, key=lambda r: order(tag_version(r["tag_name"])), default=None)


def preview_version(base, day, tags):
    """The version of a new preview after the release `base`, built on `day` (YYYYMMDD), with `tags` taken."""
    stem = f"{next_patch(base)}-preview.{day}"
    same_day = [order(v)[-1] for v in map(tag_version, tags) if v and (v == stem or v.startswith(stem + "."))]
    return f"{stem}.{max(same_day) + 1}" if same_day else stem


# ---- GitHub --------------------------------------------------------------------------------------

class GitHubError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def call(token, path, method="GET", body=None):
    """One request to GitHub's API; `body`, when given, is sent as JSON (.github/scripts/release.py creates tags)."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "sedjem-preview-plan"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(API + path, data=data, method=method, headers=headers),
                                    timeout=60) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise GitHubError(e.code, e.read().decode(errors="replace")[:300]) from None
    return json.loads(data) if data else None


def ref(name):
    return urllib.parse.quote(name, safe="")


def list_releases(repo, api):
    """Every release, drafts included when the token may see them."""
    found, page = [], 1
    while True:
        batch = api(f"/repos/{repo}/releases?per_page=100&page={page}")
        found += batch
        if len(batch) < 100:
            return found
        page += 1


def compare(repo, api, base, head):
    """Where head stands against base: "ahead" (newer), "identical", "behind" (older) or "diverged"."""
    return api(f"/repos/{repo}/compare/{ref(base)}...{ref(head)}?per_page=1")["status"]


def tested(run, repo):
    """A Tests run that passed for a push to main of this repository. Its conclusion is that of all its jobs,
    so the tests passed on both systems. A pull request from a fork whose branch is also called main is not
    one, nor is a run still going or one that failed."""
    return (run.get("event") == "push" and run.get("head_branch") == BRANCH and run.get("conclusion") == "success"
            and bool(SHA.fullmatch(run.get("head_sha") or ""))
            and str((run.get("head_repository") or {}).get("full_name", "")).lower() == repo.lower())


# ---- the subcommands -----------------------------------------------------------------------------

def plan(repo, api, day):
    """The nightly's decision: {"build": bool, "why": str, "version": str or None, "commit": str or None}."""
    def no(why):
        return {"build": False, "why": why, "version": None, "commit": None}

    releases = list_releases(repo, api)
    base = newest([r for r in releases if is_release(r)])
    if base is None:
        return no("there is no release yet, and previews follow one")
    runs = api(f"/repos/{repo}/actions/workflows/{TESTS}/runs?branch={BRANCH}&event=push&status=success&per_page=20")
    run = next((r for r in runs.get("workflow_runs", []) if tested(r, repo)), None)
    if run is None:
        return no(f"no commit on {BRANCH} has passed the Tests workflow")
    commit = run["head_sha"]
    if compare(repo, api, commit, BRANCH) not in ("ahead", "identical"):
        return no(f"{commit[:7]}, the newest commit whose tests passed, is no longer on {BRANCH}")
    try:  # a commit from before nightly previews has no build stamp, no installer update mode and no check
        api(f"/repos/{repo}/contents/{SELF}?ref={commit}")
    except GitHubError as e:
        if e.status != 404:
            raise
        return no(f"{commit[:7]}, the newest commit whose tests passed, is older than nightly previews")
    preview = newest([r for r in releases if is_preview(r) and not r.get("draft")])
    for kind, rel in (("release", base), ("preview", preview)):
        if rel is None:
            continue
        target = rel.get("target_commitish") or ""
        built = target if kind == "preview" and SHA.fullmatch(target) else rel["tag_name"]
        if compare(repo, api, built, commit) in ("identical", "behind"):
            return no(f"{commit[:7]}, the newest commit whose tests passed, is already in the {kind} {rel['tag_name']}")
    stem = f"{next_patch(tag_version(base['tag_name']))}-preview.{day}"
    tags = {r["tag_name"] for r in releases}
    tags |= {t["ref"].removeprefix("refs/tags/") for t in api(f"/repos/{repo}/git/matching-refs/tags/v{stem}")}
    version = preview_version(tag_version(base["tag_name"]), day, tags)
    since = f"the {preview['tag_name']} preview" if preview else f"the {base['tag_name']} release"
    return {"build": True, "why": f"{commit[:7]} passed the tests and is newer than {since}",
            "version": version, "commit": commit}


def check(version, app_version, repo, api):
    """(channel, base) for a version that may be built and published: ("stable", "") for a release,
    ("preview", tag of the release it follows) for a preview. ValueError says why it may not."""
    if RELEASE.fullmatch(version):
        if version != app_version:
            raise ValueError(f"v{version} doesn't match the version in app/__init__.py ({app_version})")
        return "stable", ""
    m = PREVIEW.fullmatch(version)
    if not m:
        raise ValueError(f"{version} is neither a release (X.Y.Z) nor a preview (X.Y.Z-preview.YYYYMMDD[.N])")
    try:
        datetime.strptime(m.group(4), "%Y%m%d")
    except ValueError:
        raise ValueError(f"{m.group(4)} in {version} is not a date") from None
    base = newest([r for r in list_releases(repo, api) if is_release(r)])
    if base is None:
        raise ValueError("there is no release yet, and a preview follows one")
    expected = next_patch(tag_version(base["tag_name"]))
    if version.split("-")[0] != expected:
        raise ValueError(f"a preview after {base['tag_name']} is {expected}-preview.YYYYMMDD[.N], not {version}")
    return "preview", base["tag_name"]


def notes(repo, version, commit, base):
    """The Markdown of a preview's pre-release."""
    day = datetime.strptime(PREVIEW.fullmatch(version).group(4), "%Y%m%d")
    url = f"https://github.com/{repo}"
    since = f" It has every change since {base} ([the list]({url}/compare/{base}...{commit}))." if base else ""
    return "\n".join([
        f"A preview of Sedjem, built automatically on {day.day} {day:%B %Y} from commit "
        f"[{commit[:7]}]({url}/commit/{commit}) on {BRANCH}.{since} Its tests passed on Windows and Linux, but "
        f"nobody has tried this build by hand, so for everyday use take the [latest release]({url}/releases/latest).",
        "",
        f"- Windows 10 and 11: `Sedjem-{version}-setup.exe` installs for your user, without admin rights. It isn't "
        "signed, so SmartScreen warns about an unknown publisher: choose More info, then Run anyway.",
        f"- Linux (x64): unpack `Sedjem-{version}-linux-x64.tar.gz` and run `Sedjem/Sedjem`.",
        "- `SHA256SUMS.txt` has the checksums of both.",
        "",
        f"A new preview is built each night when {BRANCH} has changed and its tests have passed. Previews are "
        f"removed after {KEEP_DAYS} days, except the newest.",
        ""])


def stale(releases, now, days=KEEP_DAYS):
    """The preview pre-releases to delete: older than `days`, except the newest published one."""
    previews = [r for r in releases if is_preview(r)]
    keep = newest([r for r in previews if not r.get("draft")])
    cutoff = now - timedelta(days=days)
    when = lambda r: datetime.fromisoformat((r.get("published_at") or r["created_at"]).replace("Z", "+00:00"))
    return [r for r in previews if r is not keep and when(r) < cutoff]


def prune(repo, api, now, days=KEEP_DAYS, dry_run=False):
    """Deletes stale previews and their tags; returns their tags."""
    gone = []
    for r in stale(list_releases(repo, api), now, days):  # pre-releases only: a release is never one
        if not dry_run:
            api(f"/repos/{repo}/releases/{r['id']}", "DELETE")
            if not r.get("draft"):  # a draft has no tag of its own; a published preview may own its name
                try:
                    api(f"/repos/{repo}/git/refs/tags/{ref(r['tag_name'])}", "DELETE")
                except GitHubError as e:
                    if e.status not in (404, 422):  # already gone
                        raise
        gone.append(r["tag_name"])
    return gone


def output(**values):
    """Step outputs: into $GITHUB_OUTPUT in a workflow, else printed."""
    lines = "".join(f"{k}={'' if v is None else str(v).lower() if isinstance(v, bool) else v}\n" for k, v in values.items())
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(lines)
    else:
        sys.stdout.write(lines)


def summary(text):
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan", help="whether to build a preview tonight, and its version")
    p.add_argument("--day", help="YYYYMMDD (default: today, UTC)")
    p = sub.add_parser("check", help="whether a version may be built and published")
    p.add_argument("version")
    p.add_argument("--app-version", required=True, help="__version__ in app/__init__.py")
    p = sub.add_parser("notes", help="the text of a preview's pre-release")
    p.add_argument("version")
    p.add_argument("--commit", required=True)
    p.add_argument("--base", default="", help="the release the preview follows, such as v0.2.0")
    p = sub.add_parser("prune", help="delete preview pre-releases older than a week, except the newest")
    p.add_argument("--days", type=int, default=KEEP_DAYS)
    p.add_argument("--dry-run", action="store_true", help="only list what would be deleted")
    for p in sub.choices.values():
        p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="OWNER/NAME")
    args = ap.parse_args(argv)
    if not args.repo:
        sys.exit("needs --repo (or GITHUB_REPOSITORY)")
    token = os.environ.get("GITHUB_TOKEN")
    api = lambda path, method="GET": call(token, path, method)

    if args.command == "plan":
        result = plan(args.repo, api, args.day or datetime.now(timezone.utc).strftime("%Y%m%d"))
        summary(f"Nightly preview: {'building ' + result['version'] if result['build'] else 'nothing to build'} ({result['why']})")
        output(build=result["build"], version=result["version"], commit=result["commit"], why=result["why"])
    elif args.command == "check":
        try:
            channel, base = check(args.version, args.app_version, args.repo, api)
        except ValueError as e:
            print(f"::error::{e}")
            sys.exit(1)
        print(f"{args.version}: a {channel} version" + (f", following {base}" if base else ""))
        output(channel=channel, base=base)
    elif args.command == "notes":
        if not PREVIEW.fullmatch(args.version) or not SHA.fullmatch(args.commit):
            sys.exit("notes: needs a preview version and a full commit sha")
        sys.stdout.write(notes(args.repo, args.version, args.commit, args.base))
    else:
        if not token and not args.dry_run:
            sys.exit("prune: needs GITHUB_TOKEN with contents: write")
        gone = prune(args.repo, api, datetime.now(timezone.utc), args.days, args.dry_run)
        summary(f"Previews {'that would be ' if args.dry_run else ''}removed: {', '.join(gone) or 'none'}")


if __name__ == "__main__":
    main()
