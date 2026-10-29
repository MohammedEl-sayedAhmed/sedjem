#!/usr/bin/env python3
"""The winget manifests of a Sedjem release, written to packaging/winget/<version>/ for microsoft/winget-pkgs.

  python packaging/winget/make.py 0.3.1 --sha256 <hash> --release-date 2026-10-29
  python packaging/winget/make.py 0.3.1 --installer Sedjem-0.3.1-setup.exe     the hash taken from the file

Three files in winget's manifest format 1.12.0: the version, the installer and the default locale (en-US). The
installer is the release's Sedjem-<version>-setup.exe on GitHub: Inno Setup, x64, installed for the current user,
silent with /VERYSILENT /SUPPRESSMSGBOXES /NORESTART, and recognised once installed by its product code, the AppId of
desktop/installer.iss followed by _is1. --release-date adds the day the release was published.

The first version goes to winget by hand: its folder is copied to manifests/m/MohammedElsayedAhmed/Sedjem/<version>/
in a fork of microsoft/winget-pkgs, and sent as a pull request. Later versions are sent by the Desktop build
workflow's winget job (.github/workflows/desktop.yml) with wingetcreate, which starts from the newest version that
winget has. Previews never go to winget. Standard library only.
"""
import argparse
import hashlib
import json
import re
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
IDENTIFIER = "MohammedElsayedAhmed.Sedjem"
MANIFEST_VERSION = "1.12.0"
REPO = "https://github.com/MohammedEl-sayedAhmed/sedjem"
AUTHOR = "Mohammed El-sayed Ahmed"
VERSION = re.compile(r"\d+\.\d+\.\d+")  # a release; previews never go to winget
SHA256 = re.compile(r"[0-9A-Fa-f]{64}")
DATE = re.compile(r"\d{4}-\d\d-\d\d")

SHORT = ("Transcripts of Egyptian Arabic–English meetings and calls, made on your own computer, with each speaker "
         "told apart by voice.")
DESCRIPTION = (
    "Sedjem transcribes recordings of meetings and calls in Egyptian Arabic mixed with English on your own computer, "
    "and labels each speaker by voice. It runs open speech models that keep English terms in English (Cohere "
    "Transcribe Arabic, a whisper-medium fine-tune for code-switching, and Whisper large-v3), or a hosted service "
    "that you choose, with your own API key. Transcripts play in step with the audio, can be corrected, compared "
    "between models and kept in versions, and export as plain text, SRT or WebVTT subtitles, Markdown or JSON. It "
    "also transcribes YouTube videos and playlists, and answers assistants such as Claude over MCP. The models are "
    "downloaded from inside the app, and a graphics card is used when one works.")
TAGS = ["arabic", "egyptian-arabic", "english", "meeting-notes", "offline", "speaker-diarization", "speech-to-text",
        "subtitles", "transcription", "whisper"]

# a string YAML reads back as itself without quotes: no indicator at the start, no ": " or " #" inside
PLAIN = re.compile(r"(?![-?:,\[\]{}#&*!|>'\"%@`\s])(?!.*(?:: | #|\s$|:$)).+")
WORDS = {"true", "false", "yes", "no", "on", "off", "null", "~", "y", "n"}


class Folded(str):
    """Text written as a folded block (>-) in lines of at most 100 characters, which reads as one paragraph."""


def scalar(value):
    """A YAML scalar: plain when it reads back as the same string, otherwise in double quotes (as a JSON string)."""
    text = str(value)
    if PLAIN.fullmatch(text) and text.lower() not in WORDS:
        return text
    return json.dumps(text, ensure_ascii=False)


def lines(fields, indent=""):
    """A mapping as YAML lines, in winget's layout: list items at the indent of their key."""
    out = []
    for key, value in fields.items():
        if isinstance(value, Folded):
            out.append(f"{indent}{key}: >-")
            out += [f"{indent}  {line}" for line in textwrap.wrap(value, 98 - len(indent))]
        elif isinstance(value, dict):
            out.append(f"{indent}{key}:")
            out += lines(value, indent + "  ")
        elif isinstance(value, list):
            out.append(f"{indent}{key}:")
            for item in value:
                if isinstance(item, dict):
                    first, *rest = lines(item, indent + "  ")
                    out += [f"{indent}- {first.lstrip()}", *rest]
                else:
                    out.append(f"{indent}- {scalar(item)}")
        else:
            out.append(f"{indent}{key}: {scalar(value)}")
    return out


def document(kind, fields):
    """One manifest file: a note on where it comes from, the schema for editors, the fields and the manifest type."""
    return "\n".join([f"# Made by packaging/winget/make.py in {REPO}",
                      f"# yaml-language-server: $schema=https://aka.ms/winget-manifest.{kind}.{MANIFEST_VERSION}.schema.json",
                      "", *lines({**fields, "ManifestType": kind, "ManifestVersion": MANIFEST_VERSION}), ""])


def product_code(iss=ROOT / "desktop" / "installer.iss"):
    """How Windows lists the installation: the AppId of desktop/installer.iss ({{ is Inno Setup's {) and _is1."""
    return re.search(r"^AppId=\{(\{[0-9A-Fa-f-]+\})\s*$", iss.read_text(encoding="utf-8"), re.M).group(1) + "_is1"


def manifests(version, sha256, release_date=None):
    """{file name: text} of a release's three manifests. ValueError for a version that isn't a release, a hash that
    isn't a SHA-256 or a date that isn't YYYY-MM-DD."""
    if not VERSION.fullmatch(version):
        raise ValueError(f"{version!r} isn't the version of a release (X.Y.Z)")
    if not SHA256.fullmatch(sha256):
        raise ValueError(f"{sha256!r} isn't a SHA-256")
    if release_date is not None and not DATE.fullmatch(release_date):
        raise ValueError(f"{release_date!r} isn't a date (YYYY-MM-DD)")
    package = {"PackageIdentifier": IDENTIFIER, "PackageVersion": version}
    installer = {**package, "InstallerType": "inno", "Scope": "user",
                 "InstallerSwitches": {"Silent": "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART",
                                       "SilentWithProgress": "/SILENT /SUPPRESSMSGBOXES /NORESTART"},
                 "UpgradeBehavior": "install", "ProductCode": product_code()}
    if release_date:
        installer["ReleaseDate"] = release_date
    installer["Installers"] = [{"Architecture": "x64",
                                "InstallerUrl": f"{REPO}/releases/download/v{version}/Sedjem-{version}-setup.exe",
                                "InstallerSha256": sha256.upper()}]
    locale = {**package, "PackageLocale": "en-US", "Publisher": AUTHOR,
              "PublisherUrl": "https://github.com/MohammedEl-sayedAhmed", "PublisherSupportUrl": f"{REPO}/issues",
              "PrivacyUrl": f"{REPO}/blob/main/docs/privacy.md", "Author": AUTHOR, "PackageName": "Sedjem",
              "PackageUrl": REPO, "License": "AGPL-3.0-only", "LicenseUrl": f"{REPO}/blob/main/LICENSE",
              "Copyright": f"Copyright (C) 2026 {AUTHOR}", "ShortDescription": SHORT,
              "Description": Folded(DESCRIPTION), "Moniker": "sedjem", "Tags": TAGS,
              "ReleaseNotesUrl": f"{REPO}/releases/tag/v{version}"}
    return {f"{IDENTIFIER}.yaml": document("version", {**package, "DefaultLocale": "en-US"}),
            f"{IDENTIFIER}.installer.yaml": document("installer", installer),
            f"{IDENTIFIER}.locale.en-US.yaml": document("defaultLocale", locale)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write the winget manifests of a Sedjem release (see the top of this file).")
    ap.add_argument("version", help="the release, such as 0.3.0")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--sha256", help="the SHA-256 of the release's Sedjem-<version>-setup.exe")
    source.add_argument("--installer", type=Path, help="that file, to take its SHA-256 from")
    ap.add_argument("--release-date", help="the day the release was published, YYYY-MM-DD")
    ap.add_argument("--out", type=Path, default=HERE, help="where the <version> folder goes (default: packaging/winget)")
    args = ap.parse_args(argv)
    if args.installer:
        with open(args.installer, "rb") as f:
            sha256 = hashlib.file_digest(f, "sha256").hexdigest()
    else:
        sha256 = args.sha256
    try:
        files = manifests(args.version, sha256, args.release_date)
    except ValueError as e:
        sys.exit(f"packaging/winget/make.py: {e}")
    folder = args.out / args.version
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8", newline="\n")
        print(folder / name)


if __name__ == "__main__":
    main()
