"""Made-up numbers for the website's tests (tests/test_site.py and tests/test_ui_site.py): the stats branch's files as
.github/scripts/repo_stats.py writes them, and docs/usage.json as tools/usage_totals.py writes it, large enough to
need thousands separators. Nothing here is real, and nothing is fetched.
"""
import json
from datetime import date, timedelta
from pathlib import Path

DAY = date(2026, 12, 2)  # the day of the newest stats
# The downloads on the two newest days: two releases, numbered so that 0.10.0 is newer than 0.9.2 only as numbers, a
# preview, and the checksum lists, which aren't the app
DOWNLOADS = [
    ("2026-12-01", "v0.9.2", "Sedjem-0.9.2-setup.exe", 400),
    ("2026-12-01", "v0.10.0", "Sedjem-0.10.0-setup.exe", 1100),
    ("2026-12-02", "v0.9.2", "Sedjem-0.9.2-setup.exe", 402),
    ("2026-12-02", "v0.9.2", "Sedjem-0.9.2-linux-x64.tar.gz", 61),
    ("2026-12-02", "v0.9.2", "SHA256SUMS.txt", 9),
    ("2026-12-02", "v0.10.0", "Sedjem-0.10.0-setup.exe", 1180),
    ("2026-12-02", "v0.10.0", "Sedjem-0.10.0-linux-x64.tar.gz", 96),
    ("2026-12-02", "v0.10.0", "SHA256SUMS.txt", 12),
    ("2026-12-02", "v0.10.1-preview.20261201", "Sedjem-0.10.1-preview.20261201-setup.exe", 7),
]
TOTAL, LATEST, RELEASE = 402 + 61 + 1180 + 96 + 7, 1180 + 96, "0.10.0"
STARS = 1021
# Page views for the 40 days to the day before DAY (GitHub's traffic numbers lag a day)
VIEWS = [(DAY - timedelta(days), 3 + days * 7 % 11, 1 + days % 4) for days in range(40, 0, -1)]
VIEWS_30 = sum(n for d, n, _ in VIEWS if d >= DAY - timedelta(30))
# The newest month of the totals, and an older one the page must not show
APP = {"month": "2026-11", "installs": 1204, "countries": 23, "transcriptions": 5310, "hours": 2750.5,
       "model": "whisper_medium"}
WEBSITE = {"page_views": 12345, "countries": 31, "clicks": 678}


def totals(month, installs, countries, transcriptions, hours, models):
    """An app month as tools/usage_totals.py writes it, with the parts the page reads filled in."""
    return {"month": month, "exported": "2026-12-03",
            "installs": {"active": installs, "new": 40, "countries": {"count": countries, "installs": {"EG": 900, "other": installs - 900}}},
            "runs": {"transcriptions": transcriptions, "hours": hours, "models": models, "hosted_share": 0.1},
            "skipped": 0}


USAGE = {
    "about": "Made up for the website's tests",
    "months": {
        "2026-10": totals("2026-10", 7, 2, 19, 4.0, {"cohere": 19, "whisper_medium": 0, "whisper_large": 0,
                                                       "other_local": 0, "hosted": 0}),
        "2026-11": totals("2026-11", 1204, 23, 5310, 2750.5, {"cohere": 1800, "whisper_medium": 2900, "whisper_large": 110,
                                                               "other_local": 0, "hosted": 500}),
    },
    "website": {
        "2026-10": {"month": "2026-10", "page_views": 99, "countries": {"count": 1, "page_views": {"other": 99}},
                    "downloads": {"clicks": 3, "platforms": {"windows": 3, "linux": 0, "source": 0}}},
        "2026-11": {"month": "2026-11", "exported": "2026-12-03", "page_views": 12345, "visitors": 7000,
                    "countries": {"count": 31, "page_views": {"EG": 9000, "other": 3345}},
                    "pages": {"en": 6000, "ar": 6300, "404": 45}, "devices": {"phone": 7000, "tablet": 345, "desktop": 5000},
                    "referrers": {"none": 8000, "sites": {"linkedin.com": 3000, "other": 1345}},
                    "downloads": {"clicks": 678, "platforms": {"windows": 600, "linux": 60, "source": 18}}, "skipped": 0},
    },
}


def write_stats(folder, downloads=DOWNLOADS, stars=STARS, views=VIEWS):
    """The three stats files in folder, as repo_stats.py writes them; returns the folder."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    rows = lambda header, rows: header + "\n" + "".join(",".join(map(str, r)) + "\n" for r in rows)  # noqa: E731
    (folder / "downloads.csv").write_text(rows("date,release,asset,downloads", downloads), encoding="utf-8")
    (folder / "repo.csv").write_text(rows("date,stars,forks,watchers", [("2026-12-01", stars - 4, 30, 9),
                                                                        (DAY.isoformat(), stars, 31, 9)]), encoding="utf-8")
    (folder / "views.csv").write_text(rows("date,views,unique", [(d.isoformat(), n, u) for d, n, u in views]), encoding="utf-8")
    return folder


def write_usage(path, data=USAGE):
    """docs/usage.json with the made-up totals; returns its path."""
    path = Path(path)
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return path
