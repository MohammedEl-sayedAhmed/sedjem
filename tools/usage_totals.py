"""Public totals of Sedjem's anonymous usage counts (app/usage.py, docs/13-usage-counts.md) and of its website's
(site/counts.js, docs/privacy.md), from Aptabase's monthly exports.

Once a month, a few days into it:
  1. In Aptabase (eu.aptabase.com), open Sedjem's Export page and download the Release events of last month, and of
     this month so far, as CSV. An event is stamped when it is sent, so one sent late (after a few days offline)
     is in this month's export although it counts for last month. Download last month's events of the "Sedjem
     website" app too.
  2. python tools/usage_totals.py path/to/last-month.csv path/to/this-month.csv --website path/to/website.csv
  3. Read the changes to docs/usage.json, README.md and docs/13-usage-counts.md, and commit them. The website shows
     the newest month once the change is on main.
  4. Delete the exports. They have every event with its daily user hash and session id, so they never go into the
     repository.

The script totals one month, last month by default: the active_month events of that month (their "month"), the
week_summary events of its weeks (a week counts in the month that holds its Thursday), and the install events
sent in it. Events without a month or week, from versions before 0.3.1, count by their time. It adds the month to
docs/usage.json (run again for the same month, it replaces it), and writes the newest month into README.md and
every month into docs/13-usage-counts.md, between their <!-- usage-totals:start --> and <!-- usage-totals:end -->
lines.

Only totals are written: counts, sums, shares and averages. A country is listed only when at least 3 installs were
active there that month, and so is a region, a system, a version of a system or of Sedjem, and a locale; the rest
are added up as "other", so that no one install can be picked out.

The website's totals go into docs/usage.json under "website", by month: the page views, and the visitors (one a
day for each of Aptabase's daily hashes, added up), the countries, the pages by language, the phones, tablets and
desktops, the sites that linked to it, and the download clicks by platform. A country or a site is listed only when
at least 3 visitors came from it that month, and at most 10 sites are, so that no one visit can be picked out. An
event the pages never send (another event, a prop too many or too few, a value not on counts.js's lists) is left
out.

The exports' columns are found by their names, in any order and letter case: timestamp, event_name, os_name,
os_version, app_version, country_code, and string_props with numeric_props (or one props column); region_name and
locale are read when they are there. The props may be JSON with single quotes in place of double ones. A missing
column stops the script with its name. Anyone can post events with the app key, which is public, so what Sedjem
never sends is left out: a week_summary whose numbers aren't whole numbers up to what a week can hold is skipped,
and a value Sedjem never sends (or a name that isn't plain text) counts as "unknown" or "other".

Options: --website (the website's exports; either set alone totals only its part), --month 2026-11 (the month to
total; default the month before --exported), --exported 2026-12-03 (the day of the exports; default today), --root
(the repository to write into; default this one).
Standard library only; the lists of what Sedjem sends are copies of app/usage.py's, and those of what the website
sends are site/counts.js's, which tests/test_usage.py compares.
"""
import argparse
import csv
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = Path("docs/usage.json")
PAGES = {"readme": Path("README.md"), "docs": Path("docs/13-usage-counts.md")}
START, END = "<!-- usage-totals:start -->", "<!-- usage-totals:end -->"
LEAST = 3  # installs a country (or a region, a version, a locale) needs that month to be listed by name
SITES = 10  # the most sites that linked to the website listed by name

# The columns, by the names an export may give them (compared in lower case, with - and spaces as _)
COLUMNS = {"timestamp": ("timestamp",), "event": ("event_name", "eventname"), "os": ("os_name", "osname"),
           "os_version": ("os_version", "osversion"), "app_version": ("app_version", "appversion"),
           "country": ("country_code", "countrycode"), "region": ("region_name", "regionname"), "locale": ("locale",),
           "string_props": ("string_props", "stringprops"), "numeric_props": ("numeric_props", "numericprops"),
           "props": ("props", "properties"), "user": ("user_id", "userid")}
REQUIRED = ("timestamp", "event", "os", "os_version", "app_version", "country")

# What Sedjem sends (copies of app/usage.py's lists)
ENGINES = ("cohere", "whisper_medium", "whisper_large", "other_local", "hosted")
SERVICES = ("elevenlabs", "speechmatics", "gemini", "deepgram", "assemblyai", "azure", "openai", "groq", "mistral")
SPEAKER_MODELS = ("titanet_small", "titanet_large", "campp", "nemotron")
FORMATS = ("txt", "srt", "vtt", "md", "json")
LENGTHS = {"runs_under_5": "under 5", "runs_5_30": "5-30", "runs_30_60": "30-60", "runs_over_60": "over 60"}
LANGUAGES = ("ar", "en", "auto")
FAILURES = ("failed_read", "failed_model", "failed_hosted", "failed_download")
EDITS = ("edit_sessions", "renames", "merges", "branches", "restores")
SUMMARY = ("transcriptions", "minutes", *ENGINES, *SERVICES, "speakers", "speakers_given", "speakers_estimated",
           *SPEAKER_MODELS[:3], "nemotron", *LENGTHS, *(f"lang_{x}" for x in LANGUAGES), "cpu_runs", "gpu_runs",
           "gpu_fallbacks", *(f"min_{e}" for e in ENGINES), *(f"proc_{e}" for e in ENGINES), "failed", *FAILURES,
           "cancelled", "youtube", "youtube_captions", "courses_created", "compares", "combines", *EDITS, *FORMATS,
           "copies", "days_used", "starts", "assistant_requests", "assistant_saves", "model_downloads",
           "update_installs")
VALUES = {"kind": ("installer", "linux-build", "source", "other"), "ui": ("desktop", "browser"),
          "install_age": ("0", "1", "2", "3-5", "6-11", "12+"),
          "channel": ("stable", "latest"), "update_check": ("on", "off", "unasked"),
          "language": ("ar", "en", "auto", "other"),
          "speaker_model": ("titanet_small", "titanet_large", "campp", "nemotron", "other"),
          "device": ("auto", "cpu"), "mcp_write": ("on", "off"),
          "cpu": ("intel", "amd", "arm", "other"), "gpu": ("none", "intel", "amd", "nvidia", "other"),
          "gpu_vram": ("none", "under 4", "4-8", "8-16", "over 16"), "cuda": ("yes", "no"), "vulkan": ("yes", "no"),
          "ram": ("under 8", "8-16", "16-32", "over 32"), "cores": ("under 4", "4-8", "8-16", "over 16"),
          "power": ("power-saver", "balanced", "performance", "unknown"),
          "disk_free": ("under 10", "10-50", "50-200", "over 200"),
          "library": ("0", "1-9", "10-49", "50-199", "200+"), "courses": ("0", "1-4", "5-19", "20+")}
MODELS = ("cohere", "whisper_medium", "whisper_large", "other_local", "titanet_large", "campp", "nemotron")
FLAGS = (*(f"has_{m}" for m in MODELS), *(f"key_{s}" for s in SERVICES))
SETTINGS = ("channel", "update_check", "language", "speaker_model", "device", "mcp_write")
HARDWARE = ("kind", "ui", "cpu", "gpu", "gpu_vram", "cuda", "vulkan", "ram", "cores", "power", "disk_free")
# The most a week can hold: the times in minutes (a week of the local and the hosted queue both busy), the days of
# use, and any other count
MOST = {**{k: 2 * 7 * 24 * 60 for k in ("minutes", *(f"min_{e}" for e in ENGINES), *(f"proc_{e}" for e in ENGINES))},
        "days_used": 7}
MOST_RUNS = 10_000
FEATURES = {"youtube": ("youtube",), "editing": EDITS, "compare": ("compares",), "combine": ("combines",),
            "export": FORMATS, "copy": ("copies",), "assistants": ("assistant_requests",)}
PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .()_/+-]{0,59}")  # a system's or Sedjem's version as it may be published
PLACE = re.compile(r"[^\W\d_][\w .,'()/-]{0,59}")  # a region's name, in any alphabet
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")

# What the website sends (a copy of site/counts.js's EVENTS): each event with its props and their values, None for
# the referrer, a host name as HOST allows it
WEBSITE = {"page_view": {"page": ("en", "ar", "404"), "device": ("phone", "tablet", "desktop"),
                         "theme": ("light", "dark", "system"), "referrer": None},
           "download": {"platform": ("windows", "linux", "source"), "place": ("hero", "get"), "page": ("en", "ar", "404")}}
HOST = re.compile(r"[a-z0-9._:\[\]-]{1,60}")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


class ExportError(Exception):
    """An export can't be read: what is wrong, in plain words."""


def column_key(name):
    return re.sub(r"[\s-]+", "_", str(name or "").strip().lstrip("﻿").lower())


def find_columns(header):
    """{our name: the column's index}. ExportError names a column that is missing."""
    keys = [column_key(h) for h in header]
    found = {}
    for ours, names in COLUMNS.items():
        index = next((keys.index(n) for n in names if n in keys), None)
        if index is not None:
            found[ours] = index
    missing = [ours for ours in REQUIRED if ours not in found]
    if "props" not in found and not ("string_props" in found and "numeric_props" in found):
        missing.append("props")
    if missing:
        ours = missing[0]
        looked = "string_props and numeric_props, or props" if ours == "props" else " or ".join(COLUMNS[ours])
        raise ExportError(f"the export has no {ours} column (looked for {looked}); its columns are: "
                          f"{', '.join(h.strip() for h in header) or 'none'}")
    return found


def props(text):
    """A props cell as a dict: JSON, or JSON with single quotes (the export may swap them); {} when it is neither."""
    text = (text or "").strip()
    for attempt in (text, text.replace("'", '"')):
        try:
            data = json.loads(attempt)
        except ValueError:
            continue
        return data if isinstance(data, dict) else {}
    return {}


def whole(value, most):
    """A count as an int, or None when it isn't a whole number from 0 to most."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() and 0 <= number <= most else None


def week_month(week):
    """The month that holds an ISO week's Thursday ("2026-W44" → "2026-10"), or None for what isn't a week."""
    m = re.fullmatch(r"(\d{4})-W(\d\d)", str(week or ""))
    try:
        return date.fromisocalendar(int(m[1]), int(m[2]), 4).strftime("%Y-%m") if m else None
    except ValueError:
        return None


def read(paths):
    """The exports' rows: {"month" (the month it counts for), "event", "os", "os_version", "app_version", "country",
    "region", "locale", "props", "visitor"}. The visitor is the day and Aptabase's daily hash, to count visitors
    by, or the row's own number when the export has no user column."""
    out = []
    for path in [paths] if isinstance(paths, (str, Path)) else paths:
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))
        except OSError as e:
            raise ExportError(f"can't read {path}: {e.strerror or e}") from None
        if not rows:
            raise ExportError(f"{path} is empty")
        cols = find_columns(rows[0])
        for row in rows[1:]:
            if not any(cell.strip() for cell in row):
                continue
            cell = lambda name: row[cols[name]].strip() if name in cols and cols[name] < len(row) else ""  # noqa: E731,B023
            merged = {**props(cell("string_props")), **props(cell("numeric_props")), **props(cell("props"))}
            event, sent = cell("event"), cell("timestamp")[:7]
            month = sent
            if event == "active_month" and MONTH.fullmatch(str(merged.get("month") or "")):
                month = merged["month"]
            elif event == "week_summary":
                month = week_month(merged.get("week")) or sent
            out.append({"month": month, "event": event, "os": cell("os"), "os_version": cell("os_version"),
                        "app_version": cell("app_version"), "country": cell("country").upper(),
                        "region": cell("region"), "locale": cell("locale").lower(), "props": merged,
                        "visitor": (cell("timestamp")[:10], cell("user")) if cell("user") else len(out)})
    return out


def fold(counts, least=LEAST):
    """The values with at least `least` installs, most first, and the rest added up as "other"."""
    shown = {k: n for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n >= least and k != "other"}
    rest = sum(counts.values()) - sum(shown.values())
    return {**shown, **({"other": rest} if rest else {})}


def fold_visitors(rows, key, least=LEAST, most=None):
    """Page views by the value key(row) gives, listed by name, most first, only for a value at least `least` visitors
    came with (and at most `most` of them); the rest are added up as "other"."""
    views, visitors = Counter(), {}
    for r in rows:
        views[key(r)] += 1
        visitors.setdefault(key(r), set()).add(r["visitor"])
    shown = [k for k in sorted(views, key=lambda k: (-views[k], k)) if len(visitors[k]) >= least and k != "other"]
    shown = {k: views[k] for k in shown[:most]}
    rest = sum(views.values()) - sum(shown.values())
    return {**shown, **({"other": rest} if rest else {})}


def share(part, whole_):
    return round(part / whole_, 3) if whole_ else None


def ratio(part, whole_):
    return round(part / whole_, 2) if whole_ else None


def spread(rows, key):
    """How many active installs had each value of a prop, in the order Sedjem's values have; "unknown" for a missing
    value (from 0.3.0) or one Sedjem never sends."""
    found = Counter(r["props"].get(key) if r["props"].get(key) in VALUES[key] else "unknown" for r in rows)
    return {v: found[v] for v in (*VALUES[key], "unknown") if found[v]}


def week_summaries(rows):
    """The week summaries that hold only what Sedjem sends, as {prop: whole number} with the props they have (a
    summary from 0.3.0 has fewer), and how many were left out."""
    good, skipped = [], 0
    for r in rows:
        numbers = {k: whole(r["props"][k], MOST.get(k, MOST_RUNS)) for k in SUMMARY if k in r["props"]}
        if None in numbers.values() or "transcriptions" not in numbers or week_month(r["props"].get("week")) is None:
            skipped += 1
        else:
            good.append({**numbers, "week": r["props"]["week"]})
    return good, skipped


def totals(rows, month, exported):
    """That month's totals: counts, sums, shares and averages only, with the rare countries, regions, versions and
    locales added up as "other"."""
    rows = [r for r in rows if r["month"] == month]
    active = [r for r in rows if r["event"] == "active_month"]
    weeks, skipped = week_summaries([r for r in rows if r["event"] == "week_summary"])
    sums = {k: sum(w.get(k, 0) for w in weeks) for k in SUMMARY}
    runs = sum(sums[k] for k in ENGINES)
    plain = lambda text: text if PLAIN.fullmatch(text) else "other"  # noqa: E731
    countries = Counter(r["country"] if re.fullmatch(r"[A-Z]{2}", r["country"]) else "other" for r in active)
    regions = Counter(f"{r['country']}: {r['region']}" if re.fullmatch(r"[A-Z]{2}", r["country"])
                      and PLACE.fullmatch(r["region"]) else "other" for r in active)
    locales = Counter(r["locale"] if re.fullmatch(r"[a-z]{2}(-[a-z]{2})?", r["locale"]) else "other" for r in active)
    flagged = lambda key: [r["props"][key] for r in active if r["props"].get(key) in (0, 1)  # noqa: E731
                           and not isinstance(r["props"].get(key), bool)]
    having = lambda key: [w for w in weeks if key in w]  # noqa: E731
    days = [w["days_used"] for w in weeks if w.get("days_used")]
    starts = having("starts")
    ended = sums["transcriptions"] + sums["youtube_captions"] + sums["failed"] + sums["cancelled"]
    tried = sums["gpu_runs"] + sums["gpu_fallbacks"]
    hours = round(sums["minutes"] / 60, 1)
    return {
        "month": month, "exported": exported,
        "installs": {"active": len(active), "new": sum(1 for r in rows if r["event"] == "install"),
                     "countries": {"count": len([c for c in countries if c != "other"]), "installs": fold(countries)},
                     "regions": fold(regions), "systems": fold(Counter(plain(r["os"]) for r in active)),
                     "system_versions": fold(Counter(plain(f"{r['os']} {r['os_version']}".strip()) for r in active)),
                     "versions": fold(Counter(plain(r["app_version"]) for r in active)), "locales": fold(locales)},
        "retention": {"install_age": spread(active, "install_age")},
        "settings": {**{k: {v: share(n, len(active)) for v, n in spread(active, k).items()} for k in SETTINGS},
                     "update_check_on": share(sum(1 for r in active if r["props"].get("update_check") == "on"),
                                              sum(1 for r in active if r["props"].get("update_check") in VALUES["update_check"]))},
        "hardware": {k: spread(active, k) for k in HARDWARE},
        "library": {k: spread(active, k) for k in ("library", "courses")},
        "adoption": {"models": {m: share(sum(flagged(f"has_{m}")), len(flagged(f"has_{m}"))) for m in MODELS},
                     "hosted_keys": {s: share(sum(flagged(f"key_{s}")), len(flagged(f"key_{s}"))) for s in SERVICES}},
        "weekly": {"active_installs": dict(sorted(Counter(w["week"] for w in weeks).items())),
                   "days_used": ratio(sum(days), len(days)),
                   "starts": ratio(sum(w["starts"] for w in starts), len(starts)),
                   "transcriptions": ratio(sums["transcriptions"], len(weeks)), "hours": ratio(hours, len(weeks))},
        "runs": {"transcriptions": sums["transcriptions"], "hours": hours,
                 "models": {e: sums[e] for e in ENGINES}, "hosted_share": share(sums["hosted"], runs),
                 "hosted_services": {s: sums[s] for s in SERVICES},
                 "speaker_models": {s: sums[s] for s in SPEAKER_MODELS},
                 "speakers": {"labelled": sums["speakers"], "given": sums["speakers_given"],
                              "estimated": sums["speakers_estimated"]},
                 "lengths": {name: sums[k] for k, name in LENGTHS.items()},
                 "languages": {x: sums[f"lang_{x}"] for x in LANGUAGES}},
        "devices": {"cpu_runs": sums["cpu_runs"], "gpu_runs": sums["gpu_runs"], "gpu_fallbacks": sums["gpu_fallbacks"],
                    "gpu_share": share(sums["gpu_runs"], sums["gpu_runs"] + sums["cpu_runs"]),
                    "fallback_rate": share(sums["gpu_fallbacks"], tried),
                    "real_time_factor": {e: ratio(sums[f"proc_{e}"], sums[f"min_{e}"]) for e in ENGINES}},
        "rates": {"ended": ended, **{k: share(sums[k], ended) for k in ("failed", *FAILURES, "cancelled")}},
        "features": {"weeks_using": {name: share(sum(1 for w in weeks if all(k in w for k in keys)
                                                     and any(w[k] for k in keys)),
                                                 sum(1 for w in weeks if all(k in w for k in keys)))
                                     for name, keys in FEATURES.items()},
                     **{k: sums[k] for k in ("youtube", "youtube_captions", "courses_created", "compares", "combines",
                                             *EDITS, "copies", "assistant_requests", "assistant_saves",
                                             "model_downloads", "update_installs")},
                     "exports": {k: sums[k] for k in FORMATS}},
        "skipped": skipped,
    }


def website_events(rows):
    """The website's events that hold exactly what its pages send, and how many others were left out."""
    good, skipped = [], 0
    for r in rows:
        allowed = WEBSITE.get(r["event"])
        props = r["props"]
        if allowed and set(props) == set(allowed) and all(
                isinstance(props[k], str) and (props[k] in values if values else HOST.fullmatch(props[k]))
                for k, values in allowed.items()):
            good.append(r)
        else:
            skipped += 1
    return good, skipped


def website_totals(rows, month, exported):
    """That month's totals of the website's counts: page views and visitors, where they came from and what they
    opened, and the download clicks."""
    events, skipped = website_events([r for r in rows if r["month"] == month])
    views = [r for r in events if r["event"] == "page_view"]
    clicks = [r for r in events if r["event"] == "download"]
    country = lambda r: r["country"] if re.fullmatch(r"[A-Z]{2}", r["country"]) else "other"  # noqa: E731
    by = lambda rows_, prop, values: {v: sum(1 for r in rows_ if r["props"][prop] == v) for v in values}  # noqa: E731
    linked = [r for r in views if r["props"]["referrer"] != "none"]
    return {
        "month": month, "exported": exported,
        "page_views": len(views), "visitors": len({r["visitor"] for r in views}),
        "countries": {"count": len({country(r) for r in views} - {"other"}), "page_views": fold_visitors(views, country)},
        "pages": by(views, "page", WEBSITE["page_view"]["page"]),
        "devices": by(views, "device", WEBSITE["page_view"]["device"]),
        "referrers": {"none": len(views) - len(linked),
                      "sites": fold_visitors(linked, lambda r: r["props"]["referrer"], most=SITES)},
        "downloads": {"clicks": len(clicks), "platforms": by(clicks, "platform", WEBSITE["download"]["platform"])},
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------

def month_name(month):
    year, m = month.split("-")
    return f"{MONTHS[int(m) - 1]} {year}"


def last_month(day):
    """The month before a day ("2026-12-03" → "2026-11")."""
    year, month = int(day[:4]), int(day[5:7])
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


def plural(n, word, words=None):
    return f"{n:,} {word if n == 1 else words or word + 's'}"


def readme_block(t):
    """The newest month, in two sentences, for README.md."""
    i, runs = t["installs"], t["runs"]
    where = f", in {plural(i['countries']['count'], 'country', 'countries')}" if i["countries"]["count"] else ""
    hosted = f", {round(runs['hosted_share'] * 100)}% of them with hosted services" if runs["hosted_share"] is not None else ""
    return (f"In {month_name(t['month'])}, {plural(i['active'], 'install')} ran Sedjem{where}, and "
            f"{i['new']:,} {'was' if i['new'] == 1 else 'were'} new. They made "
            f"{plural(runs['transcriptions'], 'transcription')} of {runs['hours']:,} hours of audio{hosted}. Every "
            "month's totals are in [docs/usage.json](docs/usage.json).")


def docs_block(months):
    """Every month, newest first, as a table, for docs/13-usage-counts.md."""
    lines = ["| Month | Active installs | New installs | Countries | Transcriptions | Hours | Hosted |",
             "|---|---|---|---|---|---|---|"]
    for t in sorted(months.values(), key=lambda t: t["month"], reverse=True):
        i, runs = t["installs"], t["runs"]
        hosted = f"{round(runs['hosted_share'] * 100)}%" if runs["hosted_share"] is not None else "none"
        lines.append(f"| {month_name(t['month'])} | {i['active']:,} | {i['new']:,} | {i['countries']['count']} | "
                     f"{runs['transcriptions']:,} | {runs['hours']:,} | {hosted} |")
    lines.append("")
    lines.append("Each month's other totals (retention, settings, hardware, the models downloaded, weekly use, the "
                 "runs, speed, failures and features) are in [docs/usage.json](usage.json).")
    return "\n".join(lines)


def put_block(text, block, name):
    """text with what is between the two marker lines replaced by block."""
    start, end = text.find(START), text.find(END)
    if start < 0 or end < start:
        raise ExportError(f"{name} has no {START} ... {END} lines to write the totals between")
    return f"{text[:start + len(START)]}\n{block}\n{text[end:]}"


def write(root, t=None, site=None):
    """Add the app's month (t) and the website's (site) to docs/usage.json, keeping the other months, and write the
    app's totals into README.md and docs/13-usage-counts.md."""
    root = Path(root)
    path = root / OUT
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    kept = lambda key: old.get(key) if isinstance(old.get(key), dict) else {}  # noqa: E731
    months = dict(sorted({**kept("months"), **({t["month"]: t} if t else {})}.items()))
    website = dict(sorted({**kept("website"), **({site["month"]: site} if site else {})}.items()))
    data = {"about": "Monthly totals of Sedjem's anonymous usage counts (months) and of its website's (website), made "
                     "by tools/usage_totals.py from Aptabase's exports; see docs/13-usage-counts.md", "months": months,
            **({"website": website} if website else {})}
    pages = {}
    for key, page in PAGES.items() if months else ():
        text = (root / page).read_text(encoding="utf-8")
        block = readme_block(months[max(months)]) if key == "readme" else docs_block(months)  # the newest month
        pages[page] = put_block(text, block, page.as_posix())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for page, text in pages.items():
        (root / page).write_text(text, encoding="utf-8")


def exported_month(rows, month, whose):
    """ExportError when the exports hold nothing for the month, naming the months they do hold."""
    if not any(r["month"] == month for r in rows):
        found = sorted({r["month"] for r in rows if MONTH.fullmatch(r["month"])})
        raise ExportError(f"{whose} exports have nothing for {month} (they have {', '.join(found) or 'no month'}); "
                          "choose the month with --month")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("exports", type=Path, nargs="*", help="Aptabase's exports of Release events, as CSV")
    ap.add_argument("--website", type=Path, nargs="+", default=[], help="the Sedjem website app's exports, as CSV")
    ap.add_argument("--month", help="the month to total, as 2026-11 (default: the month before --exported)")
    ap.add_argument("--exported", default=date.today().isoformat(), help="the day of the exports (default today)")
    ap.add_argument("--root", type=Path, default=ROOT, help="the repository to write into (default this one)")
    args = ap.parse_args(argv)
    try:
        if args.month and not MONTH.fullmatch(args.month):
            raise ExportError(f"--month {args.month} isn't a month like 2026-11")
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])-\d\d", args.exported):
            raise ExportError(f"--exported {args.exported} isn't a day like 2026-12-03")
        if not args.exports and not args.website:
            raise ExportError("give the app's exports, the website's (--website), or both")
        month = args.month or last_month(args.exported)
        t = site = None
        if args.exports:
            rows = read(args.exports)
            exported_month(rows, month, "the app's")
            t = totals(rows, month, args.exported)
        if args.website:
            rows = read(args.website)
            exported_month(rows, month, "the website's")
            site = website_totals(rows, month, args.exported)
        write(args.root, t, site)
    except ExportError as e:
        sys.exit(f"usage_totals: {e}")
    if t:
        print(f"{month_name(t['month'])}: {t['installs']['active']} active installs, {t['installs']['new']} new, "
              f"{t['installs']['countries']['count']} countries, {t['runs']['transcriptions']} transcriptions, "
              f"{t['runs']['hours']} hours" + (f"; {t['skipped']} week summaries left out" if t["skipped"] else ""))
    if site:
        print(f"The website in {month_name(site['month'])}: {site['page_views']} page views by {site['visitors']} "
              f"visitors, {site['countries']['count']} countries, {site['downloads']['clicks']} download clicks"
              + (f"; {site['skipped']} events left out" if site["skipped"] else ""))
    print(f"Wrote {OUT.as_posix()}" + (", README.md and docs/13-usage-counts.md" if t else "") + ". Delete the exports now.")


if __name__ == "__main__":
    main()
