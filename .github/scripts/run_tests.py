"""The test suite as the workflows run it: the tests of `python -m unittest discover -s tests -v`, then a summary
of the run on the workflow run's page (the file in GITHUB_STEP_SUMMARY): how many tests passed, failed, stopped
with an error or were skipped, each failure with the end of its traceback, and the slowest tests. With --coverage,
coverage.py (requirements-dev.txt) measures the code the tests run and the summary adds its share by file; it is
shown, not enforced, so no number fails a run. The exit status is unittest's: 0 when every test passed.

    python .github/scripts/run_tests.py                  the tests and their summary, printed at the end
    python .github/scripts/run_tests.py --coverage       with the coverage
    python .github/scripts/run_tests.py --summary FILE   the summary into FILE (default: $GITHUB_STEP_SUMMARY)

Locally, `python -m unittest discover -s tests -v` runs the same tests. A browser test that fails in CI also leaves
its Playwright trace and a screenshot (tests/ui_support.py, SEDJEM_UI_TRACES), which the Tests workflow uploads.
"""
import argparse
import io
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SLOWEST = 10
TRACEBACK_LINES = 25


def case_id(test):
    """The name of a test as unittest prints it, for a subtest its test's, for a class that failed to start its
    description."""
    case = getattr(test, "test_case", test)  # a subtest
    return case.id() if isinstance(case, unittest.TestCase) else str(test)


def counts(result):
    """Tests passed, failed, stopped with an error and skipped. A test with several failed subtests counts once; a
    class that couldn't start counts as one error, or as one skip when it was skipped as a whole."""
    def tests(entries):
        return {case_id(t) for t in entries if isinstance(getattr(t, "test_case", t), unittest.TestCase)}

    failed = tests([t for t, _ in result.failures] + list(result.unexpectedSuccesses))
    errored = tests([t for t, _ in result.errors]) - failed
    outside = [t for t, _ in result.errors if not isinstance(getattr(t, "test_case", t), unittest.TestCase)]
    skipped = tests([t for t, _ in result.skipped])
    passed = result.testsRun - len(failed | errored | skipped) - len(result.expectedFailures)
    return {"passed": max(passed, 0), "failed": len(failed), "errors": len(errored) + len(outside),
            "skipped": len(result.skipped)}


def duration(seconds):
    minutes, seconds = divmod(round(seconds), 60)
    return f"{minutes} min {seconds} s" if minutes else f"{seconds} s"


def summary(result, seconds, title="Tests", coverage=None):
    """The Markdown summary of a run. `coverage` is (total percent, Markdown table by file) or None."""
    n = counts(result)
    verdict = "passed" if result.wasSuccessful() else "failed"
    lines = [f"### {title}: {verdict}", "",
             "| Passed | Failed | Errors | Skipped | Time |", "|---|---|---|---|---|",
             f"| {n['passed']} | {n['failed']} | {n['errors']} | {n['skipped']} | {duration(seconds)} |", ""]
    problems = [("Failed", t, tb) for t, tb in result.failures] + [("Error", t, tb) for t, tb in result.errors]
    problems += [("Passed, but marked as expected to fail", t, "") for t in result.unexpectedSuccesses]
    for kind, test, tb in problems:
        tail = "\n".join(tb.rstrip().splitlines()[-TRACEBACK_LINES:])
        lines += [f"<details><summary>{kind}: <code>{test}</code></summary>", "", "```text", tail, "```", "",
                  "</details>", ""]
    if result.skipped:
        lines += ["<details><summary>Skipped</summary>", ""]
        lines += [f"- `{case_id(t)}`: {reason}" for t, reason in result.skipped]
        lines += ["", "</details>", ""]
    slow = sorted(getattr(result, "collectedDurations", []), key=lambda d: -d[1])[:SLOWEST]
    if slow:
        lines += ["<details><summary>The slowest tests</summary>", "", "| Test | Seconds |", "|---|---|"]
        lines += [f"| `{name}` | {elapsed:.1f} |" for name, elapsed in slow]
        lines += ["", "</details>", ""]
    if coverage:
        total, table = coverage
        lines += [f"Coverage: {total:.0f}% of the statements ran (measured, not enforced).", "",
                  "<details><summary>Coverage by file</summary>", "", table.strip(), "", "</details>", ""]
    return "\n".join(lines)


def run(suite, stream=sys.stderr):
    """Runs a suite as `python -m unittest -v` does; returns the result and the seconds it took."""
    # warnings shown once per place, as `python -m unittest` shows them
    runner = unittest.TextTestRunner(stream=stream, verbosity=2, warnings=None if sys.warnoptions else "default")
    start = time.perf_counter()
    result = runner.run(suite)
    return result, time.perf_counter() - start


def start_coverage():
    """coverage.py, measuring the repository's code (not the tests or this script) from now on, or None when it
    isn't installed."""
    try:
        import coverage
    except ImportError:
        print("coverage.py is not installed (requirements-dev.txt): the tests run without it", file=sys.stderr)
        return None
    cov = coverage.Coverage(data_file=None, source=[str(ROOT)],
                            omit=[str(ROOT / "tests" / "*"), str(ROOT / ".github" / "*"), str(ROOT / ".venv*" / "*")])
    cov.start()
    return cov


def coverage_report(cov):
    """(total percent, Markdown table by file) of what was measured, or None when coverage.py can't report it. Code
    with no file behind it (compiled from text by a test, under a made-up name) is left out."""
    cov.stop()
    table = io.StringIO()
    try:
        total = cov.report(file=table, output_format="markdown", skip_empty=True, precision=0, ignore_errors=True)
    except Exception as e:  # coverage.py's own errors, such as nothing measured: the summary goes out without it
        print(f"coverage.py couldn't report the coverage: {e}", file=sys.stderr)
        return None
    return total, table.getvalue()


def main(argv=None):
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--coverage", action="store_true", help="measure the coverage with coverage.py")
    ap.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY"),
                    help="the file the summary is added to (default: $GITHUB_STEP_SUMMARY, else it is printed)")
    ap.add_argument("--title", default=f"Tests on {os.environ['RUNNER_OS']}" if os.environ.get("RUNNER_OS") else "Tests")
    ap.add_argument("--tests", default=str(ROOT / "tests"), help="the folder of the tests (default: tests/)")
    args = ap.parse_args(argv)
    cov = start_coverage() if args.coverage else None
    suite = unittest.defaultTestLoader.discover(args.tests)
    result, seconds = run(suite)
    text = summary(result, seconds, args.title, coverage_report(cov) if cov else None)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print("\n" + text)
    sys.exit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    # the repository first on the path, where `python -m unittest` from its folder has it, not this script's folder
    sys.path[0] = str(ROOT)
    main()
