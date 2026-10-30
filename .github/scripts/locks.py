"""The locks: every package the pipeline installs, each pinned to a version and to the SHA-256 of its files
(docs/14-pipeline.md). The requirement files name the packages Sedjem needs at the versions it wants; a lock adds
the packages those need in turn, for Linux and Windows on Python 3.12 (the workflows' Python), made with
`uv pip compile --universal --generate-hashes`. The workflows install only from the locks, with
`pip install --require-hashes`, so that no package can change under a run without a commit that changes its lock.

  .github/locks/ci.txt          requirements.txt, requirements-desktop.txt and requirements-dev.txt: the test jobs
                                and the Desktop build, which bundles what it installs
  .github/locks/source.txt      requirements.txt and requirements-desktop.txt, at ci.txt's versions: the setup
                                from source on Windows (the Tests workflow's windows-source job)
  .github/locks/setuptools.txt  setuptools at ci.txt's version, installed first: pywebview needs proxy-tools,
                                which comes only as source code, and pip builds it with this setuptools when told
                                --no-build-isolation. In an isolated build pip would install the newest setuptools
                                without checking a hash
  .github/locks/tools.txt       the pipeline's tools: ruff at requirements-dev.txt's version, uv and pip-audit at
                                .github/locks/tools.in's

  make   (the default) make the locks again, after a requirement file changed, and commit them. A package keeps
         the version it has in the lock while the requirements allow it; --upgrade-package NAME (again for more)
         moves a package to its newest version allowed, --upgrade every package. Each lock is then resolved again
         for Linux and for Windows with its versions, and what a system needs that the one resolution for both
         missed is added, with a comment that says so. Needs uv, at the version in tools.in, and PyPI.
  check  whether each lock was made from the files as they are and left as it was made: the last line of its
         header holds the SHA-256 of both, and of what it was made with. The quick checks of the Tests
         workflow run this, to fail fast when a requirement file changed without its lock. Standard library only.
  pins   a lock's packages as NAME==VERSION, without markers and hashes: those of every system, not only the one
         it runs on. The Security workflow gives them to pip-audit.

    python .github/scripts/locks.py
    python .github/scripts/locks.py --upgrade-package numpy
    python .github/scripts/locks.py check
    python .github/scripts/locks.py pins .github/locks/ci.txt
"""
import argparse
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ".github/locks"
PYTHON = "3.12"  # actions/setup-python's python-version in the workflows
COMMAND = "python .github/scripts/locks.py"  # what the header of each lock says made it
FORMAT = 1  # what a check line covers; a change here makes every lock out of date
# Each lock in the order they are made: its file, the requirement files it is made from (None: setuptools, given
# here) and the files that constrain its versions
LOCKS = [
    ("ci.txt", ["requirements.txt", "requirements-desktop.txt", "requirements-dev.txt"], []),
    ("source.txt", ["requirements.txt", "requirements-desktop.txt"], [f"{FOLDER}/ci.txt"]),
    ("setuptools.txt", None, [f"{FOLDER}/ci.txt"]),
    ("tools.txt", [f"{FOLDER}/tools.in"], ["requirements-dev.txt"]),
]
# The systems the workflows run on, as uv names them for --python-platform, with their sys_platform: ubuntu-24.04
# (glibc 2.39) and windows-latest
SYSTEMS = {"x86_64-manylinux_2_39": "linux", "x86_64-pc-windows-msvc": "win32"}
CHECK = re.compile(r"# Made from (.+); check sha256:([0-9a-f]{64})")
PACKAGE = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)(?:\s*;\s*([^\\]+?))?\s*\\?")
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def sources(inputs, constraints):
    """What a lock is made from, as its check line names it."""
    return " ".join([*(inputs or ["setuptools"]), *constraints])


def requirement_lines(text):
    """A requirement file's requirements and options, without comments, blank lines and spaces."""
    lines = []
    for line in text.splitlines():
        line = "".join(re.split(r"(?:^|\s)#", line, maxsplit=1)[0].split())
        if line:
            lines.append(line)
    return lines


def body(text):
    """A lock without its header, the comments that open it."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if not line.startswith("#")), len(lines))
    return lines[start:]


def digest(name, inputs, constraints, lock_text, root=ROOT):
    """The SHA-256 a lock's check line holds: of what it is made from and with, and of the lock itself."""
    h = hashlib.sha256(f"sedjem locks {FORMAT}: {name}, python {PYTHON}, universal, hashes\n".encode())
    for path in [*(inputs or []), *constraints]:
        text = (root / path).read_text(encoding="utf-8")
        lines = body(text) if path.startswith(FOLDER) and path.endswith(".txt") else requirement_lines(text)
        h.update(f"{path}\n".encode() + "".join(f"{line}\n" for line in lines).encode())
    if inputs is None:
        h.update(b"setuptools\n")
    h.update(b"lock\n" + "".join(f"{line}\n" for line in body(lock_text)).encode())
    return h.hexdigest()


def packages(text):
    """The packages of a lock, in order: [(name, version, marker or None, [sha256, ...])]."""
    found = []
    for line in text.splitlines():
        m = PACKAGE.fullmatch(line.strip()) if line[:1].isalnum() else None
        if m:
            found.append((m.group(1), m.group(2), m.group(3), []))
        elif found and line.startswith(" "):
            found[-1][3].extend(HASH.findall(line))
    return found


def check(root=ROOT):
    """The problems with the locks: a lock missing, made from other files than those there now, changed after it
    was made, or a file in the folder that isn't one of them. Empty when all is well."""
    problems = []
    folder = root / FOLDER
    expected = {name for name, _, _ in LOCKS} | {"tools.in"}
    for name, inputs, constraints in LOCKS:
        path = folder / name
        if not path.is_file():
            problems.append(f"{FOLDER}/{name} is missing")
            continue
        text = path.read_text(encoding="utf-8")
        m = next(filter(None, map(CHECK.fullmatch, text.splitlines())), None)
        absent = [p for p in [*(inputs or []), *constraints] if not (root / p).is_file()]
        if not m:
            problems.append(f"{FOLDER}/{name} has no check line in its header, so it wasn't made by {COMMAND}")
        elif absent:
            problems.append(f"{FOLDER}/{name} can't be checked without {' and '.join(absent)}")
        elif m.group(1) != sources(inputs, constraints):
            problems.append(f"{FOLDER}/{name} was made from {m.group(1)}, not {sources(inputs, constraints)}")
        elif m.group(2) != digest(name, inputs, constraints, text, root):
            problems.append(f"{FOLDER}/{name} wasn't made from {sources(inputs, constraints)} as they are now, "
                            f"or was changed after it was made")
    if folder.is_dir():
        problems += [f"{FOLDER}/{p.name} isn't a lock or tools.in" for p in sorted(folder.iterdir())
                     if p.name not in expected]
    return problems


def stamp(name, inputs, constraints, root=ROOT):
    """Put the check line at the end of a lock's header, as the last line of the comments that open it."""
    path = root / FOLDER / name
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not CHECK.fullmatch(line)]
    start = next((i for i, line in enumerate(lines) if not line.startswith("#")), len(lines))
    text = "\n".join(lines) + "\n"
    line = f"# Made from {sources(inputs, constraints)}; check sha256:{digest(name, inputs, constraints, text, root)}"
    lines.insert(start, line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def uv_command():
    """How to run uv: on the PATH, or as a module of this Python."""
    uv = shutil.which("uv")
    if uv:
        return [uv]
    try:
        import uv as module  # noqa: F401
    except ImportError:
        sys.exit("needs uv: python -m pip install --require-hashes -r .github/locks/tools.txt, or "
                 "https://docs.astral.sh/uv/getting-started/installation/")
    return [sys.executable, "-m", "uv"]


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def resolution(text):
    """What a resolution without hashes holds: {name: (version, [what it is there for])}."""
    found, current = {}, None
    for line in text.splitlines():
        m = PACKAGE.fullmatch(line.strip()) if line[:1].isalnum() else None
        if m:
            current = canonical(m.group(1))
            found[current] = (m.group(2), [])
        elif current and line.lstrip().startswith("#"):
            via = re.sub(r"^#\s*(?:via\b)?\s*", "", line.strip())
            if via and not via.startswith("-"):  # a package, not a file (-r, -c)
                found[current][1].append(via)
    return found


def compile_lock(uv, name, inputs, constraints, extra, flags, root, system=None):
    """uv pip compile: the universal lock into its file, or for one system without hashes, to its output."""
    command = [*uv, "pip", "compile", "--quiet", "--python-version", PYTHON]
    if system:
        command += ["--no-header", "--python-platform", system, "--constraint", f"{FOLDER}/{name}"]
    else:
        command += ["--universal", "--generate-hashes", "--custom-compile-command", COMMAND,
                    "--output-file", f"{FOLDER}/{name}", *flags]
    for path in constraints:
        command += ["--constraint", path]
    stdin = [*([] if inputs else ["setuptools"]), *extra]
    command += [*(inputs or []), *(["-"] if stdin else [])]
    done = subprocess.run(command, cwd=root, input="".join(f"{line}\n" for line in stdin), text=True,
                          stdout=subprocess.PIPE if system else None)
    if done.returncode != 0:
        sys.exit(f"uv pip compile failed for {FOLDER}/{name}{f' on {system}' if system else ''} "
                 f"(exit code {done.returncode})")
    return done.stdout


def missing(uv, name, inputs, constraints, root):
    """What the systems the workflows run on need that the universal lock lacks, each resolved for that system with
    the lock's versions: {requirement line: [the packages that need it]}. A package whose files don't all name the
    same dependencies can mislead the universal resolution: sherpa-onnx 1.13.8's wheels need sherpa-onnx-core, which
    its source archive doesn't name."""
    have = {canonical(n) for n, _, _, _ in packages((root / FOLDER / name).read_text(encoding="utf-8"))}
    wanted = {}
    for system, platform in SYSTEMS.items():
        for package, (version, via) in resolution(compile_lock(uv, name, inputs, constraints, [], [], root,
                                                               system)).items():
            if package not in have:
                platforms, parents = wanted.setdefault(f"{package}=={version}", ([], set()))
                platforms.append(platform)
                parents.update(via)
    return {(line if len(platforms) == len(SYSTEMS) else
             f"{line} ; " + " or ".join(f"sys_platform == '{p}'" for p in platforms)): sorted(parents)
            for line, (platforms, parents) in wanted.items()}


def annotate(name, extra, root=ROOT):
    """Say in the lock why each package the systems needed besides the universal resolution is there."""
    path = root / FOLDER / name
    lines = path.read_text(encoding="utf-8").splitlines()
    for line, parents in extra.items():
        package = canonical(line.split("==")[0])
        start = next(i for i, text in enumerate(lines)
                     if text[:1].isalnum() and canonical(text.split("==")[0]) == package)
        end = next((i for i in range(start + 1, len(lines)) if lines[i][:1].isalnum()), len(lines))
        via = ", ".join(parents) or "(no package named)"
        lines.insert(end, f"    # via {via} (the universal resolution missed it; {COMMAND} adds it)")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def make(upgrade=False, upgrade_packages=(), root=ROOT):
    uv = uv_command()
    wanted = re.search(r"^uv==(\S+)", (root / FOLDER / "tools.in").read_text(encoding="utf-8"), re.M)
    have = subprocess.run([*uv, "--version"], capture_output=True, text=True).stdout.split()
    if wanted and have[1:2] != [wanted.group(1)]:
        print(f"Note: this is uv {' '.join(have[1:2]) or '(unknown)'}; the locks are made with uv {wanted.group(1)} "
              f"({FOLDER}/tools.in), and another version may write them differently.", file=sys.stderr)
    flags = [*(["--upgrade"] if upgrade else []), *(f"--upgrade-package={p}" for p in upgrade_packages)]
    for name, inputs, constraints in LOCKS:
        print(f"{FOLDER}/{name}", file=sys.stderr)
        extra = {}
        for _ in range(3):
            compile_lock(uv, name, inputs, constraints, list(extra), flags, root)
            more = missing(uv, name, inputs, constraints, root)
            if not more:
                break
            extra.update(more)
        else:
            sys.exit(f"{FOLDER}/{name} still lacks what {', '.join(SYSTEMS)} need: {', '.join(more)}")
        annotate(name, extra, root)
        stamp(name, inputs, constraints, root)


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("command", nargs="?", choices=["make", "check", "pins"], default="make")
    ap.add_argument("lock", nargs="?", type=Path, help="pins: the lock")
    ap.add_argument("--upgrade", action="store_true", help="make: every package to its newest version allowed")
    ap.add_argument("--upgrade-package", action="append", default=[], metavar="NAME",
                    help="make: this package to its newest version allowed (again for more)")
    args = ap.parse_args(argv)
    if (args.command == "pins") != (args.lock is not None):
        ap.error("pins takes a lock, and only pins does")
    if args.command == "check":
        problems = check()
        for problem in problems:
            print(f"::error::{problem}")
        if problems:
            sys.exit(f"The locks don't match the requirement files. Make them again and commit {FOLDER}/:\n"
                     f"    {COMMAND}\n(it needs uv: python -m pip install --require-hashes -r {FOLDER}/tools.txt)")
        print(f"The locks in {FOLDER}/ match the files they were made from.")
    elif args.command == "pins":
        for name, version, _, _ in packages(args.lock.read_text(encoding="utf-8")):
            print(f"{name}=={version}")
    else:
        make(args.upgrade, args.upgrade_package)


if __name__ == "__main__":
    main()
