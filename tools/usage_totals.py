"""Public totals of Sedjem's anonymous usage counts (app/usage.py, docs/13-usage-counts.md), from Aptabase's
monthly export.

Once a month, early in the month:
  1. In Aptabase (eu.aptabase.com), open Sedjem's Export page and download last month's Release events as CSV.
  2. python tools/usage_totals.py path/to/sedjem-release-2026-11-01.csv
  3. Read the changes to docs/usage.json, README.md and docs/13-usage-counts.md, and commit them.
  4. Delete the export. It has every event with its daily user hash and session id, so it never goes into the
     repository.

The script adds that month's totals to docs/usage.json (run again for the same month, it replaces them), and writes
the newest month into README.md and every month into docs/13-usage-counts.md, between their
<!-- usage-totals:start --> and <!-- usage-totals:end --> lines. Only totals are written. A country is listed only
when at least 3 installs were active there that month, and so is a system, a version of a system or a version of
Sedjem; the rest are added up as "other", so that no one install can be picked out.

The export's columns are found by their names, in any order and letter case: timestamp, event_name, os_name,
os_version, app_version, country_code, and string_props with numeric_props (or one props column). The props may
be JSON with single quotes in place of double ones. A missing column stops the script with its name. Rows of other
months and other events are skipped. Anyone can post events with the app key, which is public, so what Sedjem
never sends is left out too: a week_summary whose numbers aren't whole numbers up to what a week can hold is
skipped, and a value that isn't one Sedjem sends (or isn't plain text, for the versions) counts as "other".

Options: --month 2026-11 (when the export has rows of more than one month), --exported 2026-12-01 (the day of the
export; default today), --root (the repository to write into; default this one). Standard library only.
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
LEAST = 3  # installs a country (or a version) needs that month to be listed by name

# The columns, by the names an export may give them (compared in lower case, with - and spaces as _)
COLUMNS = {"timestamp": ("timestamp",), "event": ("event_name", "eventname"), "os": ("os_name", "osname"),
           "os_version": ("os_version", "osversion"), "app_version": ("app_version", "appversion"),
           "country": ("country_code", "countrycode"), "string_props": ("string_props", "stringprops"),
           "numeric_props": ("numeric_props", "numericprops"), "props": ("props", "properties")}
REQUIRED = ("timestamp", "event", "os", "os_version", "app_version", "country")

# What Sedjem sends (app/usage.py)
ENGINES = ("cohere", "whisper_medium", "whisper_large", "other_local", "hosted")
FORMATS = ("txt", "srt", "vtt", "md", "json")
FEATURES = ("speakers", "nemotron", "youtube", "compares", "failed")
WEEK = ("transcriptions", "minutes", *ENGINES, *FEATURES, *FORMATS)
MOST = {"minutes": 2 * 7 * 24 * 60}  # a week of the local and the hosted queue both busy, in minutes
MOST_RUNS = 10_000                  # any other count, in one week
VALUES = {"kind": ("installer", "linux-build", "source", "other"), "ui": ("desktop", "browser"),
          "gpu": ("none", "intel", "amd", "nvidia", "other"), "ram": ("under 8", "8-16", "16-32", "over 32"),
          "cores": ("under 4", "4-8", "8-16", "over 16")}
NAMES = {"cohere": "Cohere Transcribe Arabic", "whisper_medium": "whisper-medium", "whisper_large": "Whisper large-v3",
         "other_local": "other local models", "hosted": "hosted services"}
PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .()_/+-]{0,59}")  # a system's or Sedjem's version as it may be published
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")


class ExportError(Exception):
    """The export can't be read: what is wrong, in plain words."""


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


def read(path):
    """The export's rows: {"month", "event", "os", "os_version", "app_version", "country", "props"}."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.reader(f))
    except OSError as e:
        raise ExportError(f"can't read {path}: {e.strerror or e}") from None
    if not rows:
        raise ExportError(f"{path} is empty")
    cols = find_columns(rows[0])
    out = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        cell = lambda name: row[cols[name]].strip() if name in cols and cols[name] < len(row) else ""  # noqa: E731
        merged = {**props(cell("string_props")), **props(cell("numeric_props")), **props(cell("props"))}
        out.append({"month": cell("timestamp")[:7], "event": cell("event"), "os": cell("os"),
                    "os_version": cell("os_version"), "app_version": cell("app_version"),
                    "country": cell("country").upper(), "props": merged})
    return out


def pick_month(rows, month=None):
    months = sorted({r["month"] for r in rows if re.fullmatch(r"\d{4}-\d\d", r["month"])})
    if month:
        if month not in months:
            raise ExportError(f"the export has no rows of {month} (it has {', '.join(months) or 'none'})")
        return month
    if len(months) != 1:
        raise ExportError(f"the export has rows of {', '.join(months) or 'no month'}; choose one with --month")
    return months[0]


def fold(counts, least=LEAST):
    """The values with at least `least` installs, most first, and the rest added up as "other"."""
    shown = {k: n for k, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if n >= least and k != "other"}
    rest = sum(counts.values()) - sum(shown.values())
    return {**shown, **({"other": rest} if rest else {})}


def totals(rows, month, exported):
    """That month's totals: only sums and counts, with the rare countries and versions added up as "other"."""
    rows = [r for r in rows if r["month"] == month]
    active = [r for r in rows if r["event"] == "active_month"]
    weeks, skipped = [], 0
    for r in rows:
        if r["event"] != "week_summary":
            continue
        numbers = {k: whole(r["props"].get(k), MOST.get(k, MOST_RUNS)) for k in WEEK}
        if None in numbers.values() or not re.fullmatch(r"\d{4}-W\d\d", str(r["props"].get("week") or "")):
            skipped += 1
        else:
            weeks.append(numbers)
    sums = {k: sum(w[k] for w in weeks) for k in WEEK}
    runs = sum(sums[k] for k in ENGINES)
    countries = Counter(r["country"] if re.fullmatch(r"[A-Z]{2}", r["country"]) else "other" for r in active)
    plain = lambda text: text if PLAIN.fullmatch(text) else "other"  # noqa: E731
    return {
        "month": month, "exported": exported,
        "active_installs": len(active), "new_installs": sum(1 for r in rows if r["event"] == "install"),
        "countries": {"count": len([c for c in countries if c != "other"]), "installs": fold(countries)},
        "systems": fold(Counter(plain(r["os"]) for r in active)),
        "system_versions": fold(Counter(plain(f"{r['os']} {r['os_version']}".strip()) for r in active)),
        "versions": fold(Counter(plain(r["app_version"]) for r in active)),
        "installs": {k: dict(Counter(r["props"].get(k) if r["props"].get(k) in values else "other" for r in active))
                     for k, values in VALUES.items()},
        "weeks": len(weeks), "transcriptions": sums["transcriptions"], "hours": round(sums["minutes"] / 60, 1),
        "models": {k: sums[k] for k in ENGINES},
        "hosted_share": round(sums["hosted"] / runs, 3) if runs else None,
        **{k: sums[k] for k in FEATURES},
        "exports": {k: sums[k] for k in FORMATS},
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------------------------

def month_name(month):
    year, m = month.split("-")
    return f"{MONTHS[int(m) - 1]} {year}"


def plural(n, word, words=None):
    return f"{n:,} {word if n == 1 else words or word + 's'}"


def readme_block(t):
    """The newest month, in two sentences, for README.md."""
    where = f", in {plural(t['countries']['count'], 'country', 'countries')}" if t["countries"]["count"] else ""
    share = f", {round(t['hosted_share'] * 100)}% of them with hosted services" if t["hosted_share"] is not None else ""
    return (f"In {month_name(t['month'])}, {plural(t['active_installs'], 'install')} ran Sedjem{where}, and "
            f"{t['new_installs']:,} {'was' if t['new_installs'] == 1 else 'were'} new. They made "
            f"{plural(t['transcriptions'], 'transcription')} of {t['hours']:,} hours of audio{share}. Every month's "
            "totals are in [docs/usage.json](docs/usage.json).")


def docs_block(months):
    """Every month, newest first, as a table, for docs/13-usage-counts.md."""
    lines = ["| Month | Active installs | New installs | Countries | Transcriptions | Hours | Hosted |",
             "|---|---|---|---|---|---|---|"]
    for t in sorted(months.values(), key=lambda t: t["month"], reverse=True):
        share = f"{round(t['hosted_share'] * 100)}%" if t["hosted_share"] is not None else "none"
        lines.append(f"| {month_name(t['month'])} | {t['active_installs']:,} | {t['new_installs']:,} | "
                     f"{t['countries']['count']} | {t['transcriptions']:,} | {t['hours']:,} | {share} |")
    lines.append("")
    lines.append("The countries, systems, versions, models, features and exports of each month are in "
                 "[docs/usage.json](usage.json).")
    return "\n".join(lines)


def put_block(text, block, name):
    """text with what is between the two marker lines replaced by block."""
    start, end = text.find(START), text.find(END)
    if start < 0 or end < start:
        raise ExportError(f"{name} has no {START} ... {END} lines to write the totals between")
    return f"{text[:start + len(START)]}\n{block}\n{text[end:]}"


def write(root, t):
    """Add the month to docs/usage.json and write the totals into README.md and docs/13-usage-counts.md."""
    root = Path(root)
    path = root / OUT
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    months = old.get("months") if isinstance(old.get("months"), dict) else {}
    months = dict(sorted({**months, t["month"]: t}.items()))
    data = {"about": "Monthly totals of Sedjem's anonymous usage counts, made by tools/usage_totals.py from "
                     "Aptabase's export; see docs/13-usage-counts.md", "months": months}
    pages = {}
    for key, page in PAGES.items():
        text = (root / page).read_text(encoding="utf-8")
        block = readme_block(months[max(months)]) if key == "readme" else docs_block(months)  # the newest month
        pages[page] = put_block(text, block, page.as_posix())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    for page, text in pages.items():
        (root / page).write_text(text, encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("export", type=Path, help="Aptabase's export of one month's Release events, as CSV")
    ap.add_argument("--month", help="the month to total, as 2026-11 (needed when the export has several)")
    ap.add_argument("--exported", default=date.today().isoformat(), help="the day the export was made (default today)")
    ap.add_argument("--root", type=Path, default=ROOT, help="the repository to write into (default this one)")
    args = ap.parse_args(argv)
    try:
        if args.month and not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", args.month):
            raise ExportError(f"--month {args.month} isn't a month like 2026-11")
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", args.exported):
            raise ExportError(f"--exported {args.exported} isn't a day like 2026-12-01")
        rows = read(args.export)
        t = totals(rows, pick_month(rows, args.month), args.exported)
        write(args.root, t)
    except ExportError as e:
        sys.exit(f"usage_totals: {e}")
    print(f"{month_name(t['month'])}: {t['active_installs']} active installs, {t['new_installs']} new, "
          f"{t['countries']['count']} countries, {t['transcriptions']} transcriptions, {t['hours']} hours"
          + (f"; {t['skipped']} week summaries left out" if t["skipped"] else ""))
    print(f"Wrote {OUT.as_posix()}, README.md and docs/13-usage-counts.md. Delete the export now.")


if __name__ == "__main__":
    main()
