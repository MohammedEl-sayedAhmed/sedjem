"""A stand-in for GitHub's API in the update tests (tests/test_updates.py): releases with their files and a
SHA256SUMS.txt, runs of the Tests workflow, comparisons of two commits (worked out with git from a repository
when one is given) and the release files themselves, with ETags and 304 answers the way GitHub sends them.
Switches make it answer the way things go wrong: the connection drops (offline), the rate limit, answers that
aren't JSON or don't have the expected shape, a server error. Nothing is fetched from the internet.

  gh = FakeGitHub()                                    # on 127.0.0.1, a free port
  (storage / "config.toml").write_text(gh.config())    # [updates] api = "http://127.0.0.1:<port>"
  gh.release("0.3.0", notes="...", files={"Sedjem-0.3.0-setup.exe": b"..."})
  gh.run(sha)                                          # a Tests run for a push to main that passed
  gh.git = bare_repository                             # compare/ and commits/ worked out from it with git
  gh.mode = "offline" | "rate limited" | "malformed" | "odd" | "error" | None
  gh.calls                                             # [(path and query, If-None-Match sent, status)]
  gh.close()
"""
import hashlib
import json
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = "MohammedEl-sayedAhmed/sedjem"
CONCLUSIONS = ("success", "failure", "cancelled", "skipped", "timed_out", "neutral", "action_required")


class FakeGitHub:
    def __init__(self, repo=REPO):
        self.repo = repo
        self.releases = []    # newest first, as GitHub lists them
        self.runs = []        # newest first
        self.files = {}       # asset id -> bytes
        self.compares = {}    # (base, head) -> answer, used when there is no repository
        self.refs = {}        # ref -> commit for commits/<ref>, used when there is no repository
        self.git = None       # a bare git repository (or a checkout's .git) to work out compare/ and commits/ from
        self.mode = None
        self.etags = True     # send ETags and answer If-None-Match with 304
        self.calls = []
        self.next_id = 1000
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (Handler,), {"gh": self}))
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def config(self):
        """The [updates] table that points Sedjem at this stand-in (for <storage>/config.toml)."""
        return f'[updates]\nrepo = "{self.repo}"\napi = "{self.url}"\n'

    def paths(self):
        """The paths asked for so far, without their queries."""
        return [c[0].split("?")[0] for c in self.calls]

    def release(self, version, notes="", prerelease=None, draft=False, files=None, sums=True, bad_sum=None,
                commit="main", date="2026-10-21T10:00:00Z"):
        """Add a release, as the newest: tagged v<version>, a pre-release when the version is a preview's. files
        {name: bytes} become its assets, with a SHA256SUMS.txt that lists them (sums=False leaves it out; bad_sum
        names a file listed with a wrong checksum). commit is target_commitish: a release's is "main", a preview's
        its commit."""
        tag = f"v{version}"
        files = dict(files or {})
        if sums and files:
            files["SHA256SUMS.txt"] = "".join(
                f"{'0' * 64 if name == bad_sum else hashlib.sha256(data).hexdigest()}  {name}\n"
                for name, data in files.items()).encode()
        assets = []
        for name, data in files.items():
            aid, self.next_id = self.next_id, self.next_id + 1
            self.files[aid] = data
            assets.append({"id": aid, "name": name, "size": len(data), "content_type": "application/octet-stream",
                           "url": f"{self.url}/repos/{self.repo}/releases/assets/{aid}",
                           # not github.com, so the files are fetched through the API's address, from here
                           "browser_download_url": f"{self.url}/{self.repo}/releases/download/{tag}/{name}"})
        release = {"id": len(self.releases) + 1, "tag_name": tag, "name": f"Sedjem {version}", "draft": draft,
                   "prerelease": "-preview." in version if prerelease is None else prerelease, "body": notes,
                   "html_url": f"https://github.com/{self.repo}/releases/tag/{tag}", "published_at": date,
                   "target_commitish": commit, "assets": assets}
        self.releases.insert(0, release)
        return release

    def run(self, sha, conclusion="success", status="completed", event="push", branch="main", repo=None,
            date="2026-10-21T09:00:00Z", message="A change"):
        """Add a Tests run, as the newest: by default a push to main of this repository that passed."""
        self.runs.insert(0, {"id": len(self.runs) + 1, "name": "Tests", "head_sha": sha, "head_branch": branch,
                             "event": event, "status": status, "conclusion": conclusion if status == "completed" else None,
                             "head_repository": {"full_name": repo or self.repo},
                             "head_commit": {"id": sha, "message": message, "timestamp": date}})

    # ---- answers worked out with git ----------------------------------------------------------------
    def git_out(self, *args):
        p = subprocess.run(["git", f"--git-dir={self.git}", *args], capture_output=True, text=True, encoding="utf-8")
        return p.stdout if p.returncode == 0 else None

    def resolve(self, ref):
        if self.git is None:
            return self.refs.get(ref)
        out = self.git_out("rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
        return out.strip() if out else None

    def compare(self, base, head):
        """GitHub's comparison of base...head: None (404) when either is unknown or they have nothing in common."""
        if self.git is None:
            return self.compares.get((base, head))
        b, h = self.resolve(base), self.resolve(head)
        if not (b and h) or self.git_out("merge-base", b, h) is None:
            return None
        ahead = int(self.git_out("rev-list", "--count", f"{b}..{h}"))
        behind = int(self.git_out("rev-list", "--count", f"{h}..{b}"))
        commits = []
        for entry in self.git_out("log", "--reverse", "--format=%H%x1f%cI%x1f%B%x1e", f"{b}..{h}").split("\x1e"):
            if entry.strip():
                sha, date, message = entry.strip().split("\x1f", 2)
                commits.append({"sha": sha, "html_url": f"https://github.com/{self.repo}/commit/{sha}",
                                "commit": {"message": message.strip(), "author": {"date": date},
                                           "committer": {"date": date}}})
        files = self.git_out("diff", "--name-only", f"{b}...{h}").split()
        return {"status": "identical" if b == h else "ahead" if not behind else "behind" if not ahead else "diverged",
                "ahead_by": ahead, "behind_by": behind, "total_commits": ahead, "commits": commits,
                "html_url": f"https://github.com/{self.repo}/compare/{base}...{head}",
                "files": [{"filename": f, "status": "modified"} for f in files]}


class Handler(BaseHTTPRequestHandler):
    gh: FakeGitHub  # set by FakeGitHub
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send(self, status, body=b"", ctype="application/json; charset=utf-8", headers=None, etag=False):
        gh = self.gh
        tag = f'"{hashlib.sha1(body).hexdigest()}"' if etag and gh.etags else None
        if tag and self.headers.get("If-None-Match") == tag:
            status, body = 304, b""
        gh.calls.append((self.path, self.headers.get("If-None-Match"), status))
        self.send_response(status)
        if status != 304:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
        if tag:
            self.send_header("ETag", tag)
        self.send_header("X-RateLimit-Limit", "60")
        self.send_header("X-RateLimit-Remaining", "59")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if status != 304:
            self.wfile.write(body)

    def answer(self, obj, status=200):
        self.send(status, json.dumps(obj).encode(), etag=status == 200)

    def do_GET(self):
        gh, url = self.gh, urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(url.query))
        if gh.mode == "offline":  # the connection drops without an answer
            gh.calls.append((self.path, self.headers.get("If-None-Match"), None))
            self.close_connection = True
            return
        if gh.mode == "rate limited":
            return self.send(403, json.dumps({"message": "API rate limit exceeded for 127.0.0.1. (But here's the good "
                                                         "news: Authenticated requests get a higher rate limit.)"}).encode(),
                             headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 1800)})
        if gh.mode == "malformed":
            return self.send(200, b"<html><body>Unicorn!</body></html>", "text/html; charset=utf-8")
        if gh.mode == "odd":  # JSON, but not what any of these addresses answer
            return self.answer({"workflow_runs": "none", "status": "sideways", "tag_name": None})
        if gh.mode == "error":
            return self.answer({"message": "Server Error"}, 502)
        repo = f"/repos/{gh.repo}"
        path = url.path
        if path == f"{repo}/releases":
            return self.answer(gh.releases[:int(query.get("per_page", 30))])
        if path == f"{repo}/actions/workflows/tests.yml/runs":
            runs = [r for r in gh.runs if (query.get("branch") in (None, r["head_branch"]))
                    and query.get("event") in (None, r["event"])
                    and (query.get("status") is None or r["conclusion" if query["status"] in CONCLUSIONS else "status"]
                         == query["status"])]
            return self.answer({"total_count": len(runs), "workflow_runs": runs[:int(query.get("per_page", 30))]})
        if path.startswith(f"{repo}/compare/"):
            base, _, head = urllib.parse.unquote(path[len(f"{repo}/compare/"):]).partition("...")
            found = gh.compare(base, head)
            return self.answer(found) if found else self.answer({"message": "Not Found"}, 404)
        if path.startswith(f"{repo}/commits/"):
            sha = gh.resolve(urllib.parse.unquote(path[len(f"{repo}/commits/"):]))
            if not sha:
                return self.answer({"message": "No commit found for SHA"}, 422)
            if self.headers.get("Accept") == "application/vnd.github.sha":
                return self.send(200, sha.encode(), "application/vnd.github.sha; charset=utf-8", etag=True)
            return self.answer({"sha": sha})
        if path.startswith(f"{repo}/releases/assets/"):
            aid = int(path.rsplit("/", 1)[1])
            if aid not in gh.files:
                return self.answer({"message": "Not Found"}, 404)
            if self.headers.get("Accept") == "application/octet-stream":  # GitHub sends the file from elsewhere
                return self.send(302, headers={"Location": f"{gh.url}/files/{aid}"})
            return self.answer({"id": aid, "size": len(gh.files[aid])})
        if path.startswith("/files/"):
            data = gh.files.get(int(path.rsplit("/", 1)[1]))
            return self.send(200, data, "application/octet-stream") if data is not None else self.send(404)
        self.answer({"message": "Not Found"}, 404)
