"""The CodeQL ratchet: a pull request fails only on findings that main doesn't already have.

The known findings are kept as fingerprints in baseline.json on the `security-baseline` branch. Two commands,
both run by .github/workflows/codeql.yml on the SARIF files that the analysis wrote:

  check   compares the findings of a pull request with the baseline and fails on any that it doesn't hold. A
          finding that is new is a rule with a fingerprint that the baseline has fewer times than the scan. With
          no baseline branch yet, it passes with a notice: the first push to main makes the baseline. Once the
          maintainer sets the repository variable CODEQL_RATCHET_ARMED to true (RATCHET_ARMED in the job), a
          missing branch fails instead. A branch without baseline.json always fails.
  update  writes the findings of a commit on main as the new baseline, unless the baseline already comes from a
          newer commit.

A fingerprint is the rule and the hash that CodeQL puts in `partialFingerprints.primaryLocationLineHash`, a hash
of the code around the finding, so a finding survives lines added above it. A result without that hash falls
back to the rule, the file and the line. Two findings with the same fingerprint count twice, so a copy of a
finding that is already known is new.

Like .github/scripts/repo_stats.py, it talks to GitHub only through the REST API and writes the branch with the
git data API: no checkout of the branch, and no token stored by git. It needs only the standard library.
GITHUB_TOKEN reads the branch; `update` also needs it to have contents: write.

  python3 .github/scripts/codeql_ratchet.py check  --sarif codeql-sarif --repo OWNER/NAME
  python3 .github/scripts/codeql_ratchet.py update --sarif codeql-sarif --repo OWNER/NAME --commit SHA
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com"
BRANCH = "security-baseline"
FILE = "baseline.json"
RETRIES = 5


# ---- pure part: SARIF + baseline -> findings ---------------------------------------------------------

def location(result):
    """(file, line) of a result's first location, or ("?", "?")."""
    for loc in result.get("locations", []):
        physical = loc.get("physicalLocation", {})
        return (physical.get("artifactLocation", {}).get("uri", "?"),
                physical.get("region", {}).get("startLine", "?"))
    return "?", "?"


def fingerprint(result):
    rule = result.get("ruleId", "?")
    found = result.get("partialFingerprints", {}).get("primaryLocationLineHash")
    if found:
        return f"{rule}:{found}"
    path, line = location(result)
    return f"{rule}:{path}:{line}"


def findings(folder):
    """(how many times each fingerprint is in the SARIF files under the folder, one place for each), and the
    number of files read."""
    counts, places, files = Counter(), {}, sorted(Path(folder).rglob("*.sarif"))
    for path in files:
        for run in json.loads(path.read_text(encoding="utf-8")).get("runs", []):
            for result in run.get("results", []):
                fp = fingerprint(result)
                counts[fp] += 1
                places.setdefault(fp, location(result))
    return counts, places, len(files)


def new_findings(current, known):
    """The fingerprints the scan has more often than the baseline, with how many more."""
    return {fp: n - known.get(fp, 0) for fp, n in sorted(current.items()) if n > known.get(fp, 0)}


def baseline_text(counts, commit, today):
    return json.dumps({"commit": commit, "updated": today, "total": sum(counts.values()),
                       "findings": dict(sorted(counts.items()))}, indent=1) + "\n"


def escape(text):
    """Text for a workflow command: a line break in a file name must not start another command."""
    return str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


# ---- GitHub -----------------------------------------------------------------------------------------

class GitHubError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def call(token, path, method="GET", body=None, raw=False):
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28",
                                          "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
                                          "User-Agent": "codeql-ratchet"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise GitHubError(e.code, e.read().decode(errors="replace")[:300]) from None
    return data.decode() if raw else json.loads(data or b"null")


def read_baseline(repo, token):
    """(the branch's head, the baseline as a dict): no head when the branch isn't there, no baseline when the
    file isn't."""
    try:
        head = call(token, f"/repos/{repo}/git/ref/heads/{BRANCH}")["object"]["sha"]
    except GitHubError as e:
        if e.status == 404:
            return None, None
        raise
    try:
        return head, json.loads(call(token, f"/repos/{repo}/contents/{FILE}?ref={head}", raw=True))
    except GitHubError as e:
        if e.status == 404:
            return head, None
        raise


def write_baseline(repo, token, head, text, message, main_commit):
    """One commit on the branch: the files of the main commit that the findings come from, plus baseline.json. The
    branch so carries main's current workflows, the guard included: GitHub reads a push's workflows from the pushed
    commit, so a branch without codeql-baseline-guard.yml would not run it. The parent is the branch's head, and
    the branch is created when there is none. A branch that moved meanwhile refuses the update (it is not a
    fast-forward), and the caller reads it again."""
    tree = {"tree": [{"path": FILE, "mode": "100644", "type": "blob", "content": text}],
            "base_tree": call(token, f"/repos/{repo}/git/commits/{main_commit}")["tree"]["sha"]}
    sha = call(token, f"/repos/{repo}/git/trees", "POST", tree)["sha"]
    new = call(token, f"/repos/{repo}/git/commits", "POST",
               {"message": message, "tree": sha, "parents": [head] if head else []})["sha"]
    if head:
        call(token, f"/repos/{repo}/git/refs/heads/{BRANCH}", "PATCH", {"sha": new})
    else:
        call(token, f"/repos/{repo}/git/refs", "POST", {"ref": f"refs/heads/{BRANCH}", "sha": new})
    return new


def is_ahead(repo, token, old, new):
    """Whether the baseline's commit is a descendant of the commit being written, so strictly newer. The same
    commit is not: the weekly run on a main that has not moved must still write what new queries find."""
    try:
        status = call(token, f"/repos/{repo}/compare/{old}...{new}")["status"]
    except GitHubError:
        return False
    return status == "behind"


# ---- the commands -----------------------------------------------------------------------------------

def summary(lines):
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")


def check(args, token):
    current, places, files = findings(args.sarif)
    if not files:
        print(f"::error::No SARIF files under {args.sarif}: the analysis wrote nothing to compare.")
        return 1
    head, baseline = read_baseline(args.repo, token)
    # Only the CodeQL workflow writes the branch, and always with the file. A branch without it, or a missing branch
    # once the maintainer has set CODEQL_RATCHET_ARMED (after the first baseline), was changed by someone else.
    armed = os.environ.get("RATCHET_ARMED", "").strip().lower() == "true"
    if baseline is None and (head is not None or armed):
        what = f"The {BRANCH} branch has no {FILE}" if head is not None else f"The {BRANCH} branch is missing"
        print(f"::error::{what}, so there is nothing to compare with and the ratchet fails. Run the CodeQL workflow "
              f"on main by hand to write it again, and find out who changed it.")
        summary(["### CodeQL ratchet: failed, with no baseline", f"{what}. Run the CodeQL workflow on main by hand."])
        return 1
    if baseline is None:
        print(f"::notice::No {BRANCH} branch yet, so there is nothing to compare with and the ratchet passes. "
              f"The first push to main writes it. This scan has {sum(current.values())} finding(s).")
        summary(["### CodeQL ratchet: passed, with no baseline yet",
                 f"The `{BRANCH}` branch doesn't exist, so nothing was compared. The first push to main writes it."])
        return 0
    known = baseline.get("findings", {})
    new = new_findings(current, known)
    print(f"::notice::Baseline from {str(baseline.get('commit'))[:7]}: {sum(known.values())} finding(s). "
          f"This scan: {sum(current.values())}. New: {sum(new.values())}. Gone: {sum((Counter(known) - current).values())}.")
    if not new:
        summary(["### CodeQL ratchet: passed", f"No finding beyond the {sum(known.values())} in the baseline."])
        return 0
    rows = ["### CodeQL ratchet: failed", "These findings are not in the baseline:", ""]
    for fp, n in new.items():
        path, line = places.get(fp, ("?", "?"))
        rule = fp.split(":", 1)[0]
        print(f"::error file={escape(path)},line={escape(line)}::{escape(rule)}: a CodeQL finding that main doesn't have")
        rows.append(f"- `{rule}` in `{path}`, line {line}" + (f" ({n} more than the baseline)" if n > 1 else ""))
    print(f"::error::{sum(new.values())} new CodeQL finding(s). Fix them, or read them in the Security tab; "
          f"the baseline follows main, so a finding that main already has is never new.")
    summary(rows)
    return 1


def update(args, token):
    current, _, files = findings(args.sarif)
    if not files:
        print(f"::error::No SARIF files under {args.sarif}: the baseline is left as it was.")
        return 1
    today = datetime.now(timezone.utc).date().isoformat()
    for attempt in range(1, RETRIES + 1):
        head, baseline = read_baseline(args.repo, token)
        old = (baseline or {}).get("commit")
        if old and is_ahead(args.repo, token, old, args.commit):
            print(f"The baseline comes from {old[:7]}, ahead of {args.commit[:7]}; it stays.")
            return 0
        if baseline is not None and baseline.get("findings") == dict(current):
            print("The baseline already holds these findings; nothing to write.")
            return 0
        message = f"Baseline from main at {args.commit[:7]}: {sum(current.values())} finding(s)"
        try:
            new = write_baseline(args.repo, token, head, baseline_text(current, args.commit, today), message,
                                 args.commit)
        except GitHubError as e:
            if e.status not in (409, 422) or attempt == RETRIES:
                raise
            print(f"::warning::The {BRANCH} branch moved (attempt {attempt}: {e}); reading it again.")
            continue
        print(f"{BRANCH}: {new[:7]}, {sum(current.values())} finding(s) from {args.commit[:7]}")
        summary(["### CodeQL baseline written", f"{sum(current.values())} finding(s), from `{args.commit[:7]}`."])
        return 0
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("command", choices=["check", "update"])
    ap.add_argument("--sarif", required=True, help="the folder with the SARIF files")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="OWNER/NAME")
    ap.add_argument("--commit", help="update: the commit on main that the SARIF files come from")
    args = ap.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")
    if not args.repo or not token:
        sys.exit("needs --repo (or GITHUB_REPOSITORY) and GITHUB_TOKEN")
    if args.command == "update" and not args.commit:
        sys.exit("update needs --commit")
    return {"check": check, "update": update}[args.command](args, token)


if __name__ == "__main__":
    sys.exit(main())
