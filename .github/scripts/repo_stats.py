"""Daily repository statistics, kept on the `stats` branch: release downloads, clones, page views,
referrers, stars and forks.

GitHub shows clones and page views for the last 14 days only, and release downloads as running totals.
This script, run once a day by .github/workflows/stats.yml, adds the latest numbers to CSV files on the
`stats` branch, so the history reaches back further than 14 days. It also rewrites a summary there
(README.md) and two badge files that shields.io reads for the badges in the main README.

It talks to GitHub only through the REST API, never with git: GitHub counts every clone, including a
checkout by a build machine, so a daily checkout would show up as a clone of its own.

Tokens, from the environment:
  GITHUB_TOKEN  reads releases and the repository, and writes the stats branch (contents: write)
  STATS_TOKEN   reads clones, views and referrers, which need Administration read access: a fine-grained
                token for this repository. If it is missing, GITHUB_TOKEN is tried; when neither can read
                them, the traffic files are left as they were and the summary says when they were last read.

To look at the result without changing the branch, write the files into a folder instead:
    GITHUB_TOKEN=$(gh auth token) python3 .github/scripts/repo_stats.py --repo OWNER/NAME --out stats-preview
"""
import argparse
import csv
import io
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"
BRANCH = "stats"
COLOUR = "0b7f7d"  # the README's other badges
FIELDS = {
    "clones.csv": ["date", "clones", "unique"],
    "views.csv": ["date", "views", "unique"],
    "downloads.csv": ["date", "release", "asset", "downloads"],
    "referrers.csv": ["date", "referrer", "views", "unique"],
    "repo.csv": ["date", "stars", "forks", "watchers"],
}
FILES = [*FIELDS, "state.json", "README.md", "badges/downloads.json", "badges/clones.json"]


# ---- pure part: old files + fetched numbers -> new files -------------------------------------------

def read_csv(text):
    return list(csv.DictReader(io.StringIO(text or "")))


def write_csv(rows, fields):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows({k: r[k] for k in fields} for r in rows)
    return out.getvalue()


def merge_daily(rows, items, name):
    """Daily traffic rows ({date, name, unique}) updated with GitHub's buckets for the last 14 days
    ({timestamp, count, uniques}); a day GitHub reports again (today, still counting) is replaced."""
    by_date = {r["date"]: r for r in rows}
    for it in items:
        day = it["timestamp"][:10]
        by_date[day] = {"date": day, name: str(it["count"]), "unique": str(it["uniques"])}
    return [by_date[d] for d in sorted(by_date)]


def replace_day(rows, today, new):
    """A snapshot table with today's rows replaced by `new` (each given today's date)."""
    return [r for r in rows if r["date"] != today] + [{"date": today, **n} for n in new]


def is_app(asset):
    """Downloads of the app itself, not of the checksum list."""
    return not asset.lower().endswith(".txt")


def total(rows, key):
    return sum(int(r[key]) for r in rows)


def badge(label, n):
    return json.dumps({"schemaVersion": 1, "label": label, "message": f"{n:,}", "color": COLOUR}) + "\n"


def summary(tables, repo, today, traffic_read):
    clones, views = tables["clones.csv"], tables["views.csv"]
    latest = lambda rows: [r for r in rows if r["date"] == max((x["date"] for x in rows), default="")]
    downloads, referrers, info = latest(tables["downloads.csv"]), latest(tables["referrers.csv"]), latest(tables["repo.csv"])
    since = min((r["date"] for r in clones + views), default=None)
    app = [r for r in downloads if is_app(r["asset"])]
    lines = [f"# {repo.split('/')[-1].capitalize()} statistics", "",
             f"Updated on {today} by the Repository stats workflow on main (`.github/workflows/stats.yml`), from "
             "GitHub's API. GitHub itself only shows the last 14 days of clones and views; this branch keeps every day "
             + (f"since {since}." if since else "from the first run on."), ""]
    if traffic_read != today:
        lines += [f"Clones, views and referrers could not be read today (last read: {traffic_read or 'never'}). "
                  "The STATS_TOKEN secret is missing or has expired.", ""]
    lines += ["## Totals", "", "| | |", "|---|---|",
              f"| Downloads of the app, all releases | {total(app, 'downloads'):,} |"]
    if since:
        lines += [f"| Clones since {since} | {total(clones, 'clones'):,} ({total(clones, 'unique'):,} unique per day, added up) |",
                  f"| Page views since {since} | {total(views, 'views'):,} ({total(views, 'unique'):,} unique per day, added up) |"]
    if info:
        i = info[0]
        lines.append(f"| Stars, forks, watchers | {int(i['stars']):,}, {int(i['forks']):,}, {int(i['watchers']):,} |")
    if downloads:
        lines += ["", "## Downloads by release", "", "| Release | File | Downloads |", "|---|---|---|"]
        lines += [f"| {r['release']} | {r['asset']} | {int(r['downloads']):,} |" for r in downloads]
    days = sorted({r["date"] for r in clones + views}, reverse=True)[:30]
    if days:
        c, v = {r["date"]: r for r in clones}, {r["date"]: r for r in views}
        cell = lambda t, d, k: t[d][k] if d in t else "0"
        lines += ["", "## Last 30 days", "", "| Day | Clones | Unique cloners | Views | Unique visitors |", "|---|---|---|---|---|"]
        lines += [f"| {d} | {cell(c, d, 'clones')} | {cell(c, d, 'unique')} | {cell(v, d, 'views')} | {cell(v, d, 'unique')} |"
                  for d in days]
    if referrers:
        lines += ["", f"## Where visitors came from (the 14 days to {referrers[0]['date']})", "",
                  "| Site | Views | Unique visitors |", "|---|---|---|"]
        lines += [f"| {r['referrer']} | {r['views']} | {r['unique']} |" for r in referrers]
    lines += ["", "## Reading the numbers", "",
              "A unique cloner or visitor is counted once a day, so someone who clones on three days counts three "
              "times in the added-up uniques.",
              "",
              "Clones include those by build machines: every run of the Desktop build workflow clones the repository. "
              "The stats workflow does not, so it adds nothing to the counts.",
              "",
              "The CSV files beside this summary hold every number by day. The download, referrer and repository "
              "tables are snapshots: one set of rows per day, with GitHub's running totals at the time.", ""]
    return "\n".join(lines)


def update(old, fetched, repo, today):
    """New file contents from the old ones (path -> text, missing files absent) and today's numbers:
    fetched = {"releases": [...], "repo": {...}, "clones": [...] or None, "views": ..., "referrers": ...}
    (None when the traffic could not be read)."""
    tables = {name: read_csv(old.get(name)) for name in FIELDS}
    state = json.loads(old.get("state.json") or "{}")
    if fetched["clones"] is not None:
        tables["clones.csv"] = merge_daily(tables["clones.csv"], fetched["clones"], "clones")
        tables["views.csv"] = merge_daily(tables["views.csv"], fetched["views"], "views")
        tables["referrers.csv"] = replace_day(tables["referrers.csv"], today, [
            {"referrer": r["referrer"], "views": str(r["count"]), "unique": str(r["uniques"])} for r in fetched["referrers"]])
        state["traffic_read"] = today
    tables["downloads.csv"] = replace_day(tables["downloads.csv"], today, [
        {"release": rel["tag_name"], "asset": a["name"], "downloads": str(a["download_count"])}
        for rel in fetched["releases"] if not rel.get("draft") for a in rel["assets"]])
    r = fetched["repo"]
    tables["repo.csv"] = replace_day(tables["repo.csv"], today, [
        {"stars": str(r["stargazers_count"]), "forks": str(r["forks_count"]), "watchers": str(r["subscribers_count"])}])
    new = {name: write_csv(rows, FIELDS[name]) for name, rows in tables.items()}
    new["state.json"] = json.dumps(state, indent=1, sort_keys=True) + "\n"
    new["README.md"] = summary(tables, repo, today, state.get("traffic_read"))
    latest = [x for x in tables["downloads.csv"] if x["date"] == today]
    new["badges/downloads.json"] = badge("downloads", total([x for x in latest if is_app(x["asset"])], "downloads"))
    new["badges/clones.json"] = badge("clones", total(tables["clones.csv"], "clones"))
    return new


# ---- GitHub ---------------------------------------------------------------------------------------

class GitHubError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


def call(token, path, method="GET", body=None, raw=False):
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28",
                                          "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
                                          "User-Agent": "repo-stats"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        raise GitHubError(e.code, e.read().decode(errors="replace")[:300]) from None
    return data.decode() if raw else json.loads(data or b"null")


def fetch(repo, token, traffic_token):
    releases, page = [], 1
    while True:
        batch = call(token, f"/repos/{repo}/releases?per_page=100&page={page}")
        releases += batch
        if len(batch) < 100:
            break
        page += 1
    got = {"releases": releases, "repo": call(token, f"/repos/{repo}"), "clones": None, "views": None, "referrers": None}
    for tok in dict.fromkeys(t for t in (traffic_token, token) if t):  # the stats token first, then the other
        try:
            got["clones"] = call(tok, f"/repos/{repo}/traffic/clones?per=day")["clones"]
            got["views"] = call(tok, f"/repos/{repo}/traffic/views?per=day")["views"]
            got["referrers"] = call(tok, f"/repos/{repo}/traffic/popular/referrers")
            break
        except GitHubError as e:
            if e.status not in (401, 403):
                raise
            got["clones"] = got["views"] = got["referrers"] = None
    if got["clones"] is None:
        print("::warning::Clones and views could not be read: set the STATS_TOKEN secret "
              "(a fine-grained token for this repository with Administration read access).")
    return got


def read_branch(repo, token, branch):
    """(commit sha or None, {path: text}) of the stats branch."""
    try:
        head = call(token, f"/repos/{repo}/git/ref/heads/{branch}")["object"]["sha"]
    except GitHubError as e:
        if e.status == 404:
            return None, {}
        raise
    files = {}
    for path in FILES:
        try:
            files[path] = call(token, f"/repos/{repo}/contents/{path}?ref={head}", raw=True)
        except GitHubError as e:
            if e.status != 404:
                raise
    return head, files


def commit(repo, token, branch, head, files, message):
    """One commit with the given files on the branch (created, without history, if it doesn't exist)."""
    tree = {"tree": [{"path": p, "mode": "100644", "type": "blob", "content": t} for p, t in sorted(files.items())]}
    if head:
        tree["base_tree"] = call(token, f"/repos/{repo}/git/commits/{head}")["tree"]["sha"]
    sha = call(token, f"/repos/{repo}/git/trees", "POST", tree)["sha"]
    new = call(token, f"/repos/{repo}/git/commits", "POST", {"message": message, "tree": sha, "parents": [head] if head else []})["sha"]
    if head:
        call(token, f"/repos/{repo}/git/refs/heads/{branch}", "PATCH", {"sha": new})
    else:
        call(token, f"/repos/{repo}/git/refs", "POST", {"ref": f"refs/heads/{branch}", "sha": new})
    return new


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="OWNER/NAME")
    ap.add_argument("--branch", default=BRANCH)
    ap.add_argument("--out", help="write the files into this folder instead of committing them")
    args = ap.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")
    if not args.repo or not token:
        sys.exit("needs --repo (or GITHUB_REPOSITORY) and GITHUB_TOKEN")
    today = datetime.now(timezone.utc).date().isoformat()
    head, old = read_branch(args.repo, token, args.branch)
    new = update(old, fetch(args.repo, token, os.environ.get("STATS_TOKEN")), args.repo, today)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(new["README.md"])
    changed = {p: t for p, t in new.items() if old.get(p) != t}
    if args.out:
        for p, t in new.items():
            os.makedirs(os.path.dirname(os.path.join(args.out, p)) or args.out, exist_ok=True)
            with open(os.path.join(args.out, p), "w", encoding="utf-8") as f:
                f.write(t)
        print(f"wrote {len(new)} files to {args.out} ({len(changed)} differ from the {args.branch} branch)")
    elif changed:
        sha = commit(args.repo, token, args.branch, head, changed, f"Stats for {today}")
        print(f"{args.branch}: {sha[:7]}, {len(changed)} files updated")
    else:
        print("nothing changed")


if __name__ == "__main__":
    main()
