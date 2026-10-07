"""Re-sync the structural counts quoted in the documentation with the repository.

Run after any change that adds source, tests or Run_Logs. Every count a reader might check
by hand is regenerated here from the filesystem and the live test suite, so the documents
cannot drift into quoting numbers that were true three commits ago.

Usage:
    python -m pytest -q            # get the current test count
    python sync_doc_counts.py --tests 762
"""

from __future__ import annotations

import argparse
import glob
import re
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--tests", type=int, required=True, help="current passing test count")
args = parser.parse_args()

src = [p for p in glob.glob("src/*.py") if not p.endswith("__init__.py")]
tests = [p for p in glob.glob("tests/*.py") if not p.endswith("__init__.py")]


def lines(paths: list[str]) -> int:
    return sum(
        len(Path(p).read_text(encoding="utf-8", errors="replace").splitlines())
        for p in paths
    )


facts = {
    "src_modules": len(src),
    "src_lines": lines(src),
    "test_modules": len(tests),
    "test_lines": lines(tests),
    "tests": args.tests,
    "run_logs": len(glob.glob("results/run_logs/*.json")),
}
print("Repository facts:")
for key, value in facts.items():
    print(f"  {key:14s} {value:,}")

# Patterns that must track the facts above. Each is (regex, replacement).
SUBS = [
    (r"\b\d{3} tests\b", f"{facts['tests']} tests"),
    (r"\*\*\d{3} tests passing\*\*", f"**{facts['tests']} tests passing**"),
    (r"\*\*\d{3}\*\* automated tests", f"**{facts['tests']}** automated tests"),
    (r"\b\d{3} tests across \d+ modules", 
     f"{facts['tests']} tests across {facts['test_modules']} modules"),
    (r"\b\d{2} test modules\b", f"{facts['test_modules']} test modules"),
    (r"\*\*\d{2} run logs\*\*", f"**{facts['run_logs']} run logs**"),
    (r"\b\d{2} run logs\b", f"{facts['run_logs']} run logs"),
]

changed = []
for path in [*Path(".").glob("*.md"), *Path("report").glob("*.md")]:
    text = original = path.read_text(encoding="utf-8")
    for pattern, replacement in SUBS:
        text = re.sub(pattern, replacement, text)
    if text != original:
        path.write_text(text, encoding="utf-8")
        changed.append(str(path))

print(f"\nupdated {len(changed)} document(s):")
for name in changed:
    print(f"  {name}")


# Line-count claims. Kept separate from the counts above because they are approximate
# ("~9,550 lines") and are written with a leading tilde in the documents.
LINE_SUBS = [
    (r"12 source modules \(~[\d,]+ lines\)",
     f"12 source modules (~{facts['src_lines']:,} lines)"),
    (r"\d{3} tests across \d+ modules \(~[\d,]+ lines\)",
     f"{facts['tests']} tests across {facts['test_modules']} modules "
     f"(~{facts['test_lines']:,} lines)"),
    (r"\d+ test modules \(~[\d,]+ lines\)",
     f"{facts['test_modules']} test modules (~{facts['test_lines']:,} lines)"),
]

extra = []
for path in [*Path(".").glob("*.md"), *Path("report").glob("*.md")]:
    text = original = path.read_text(encoding="utf-8")
    for pattern, replacement in LINE_SUBS:
        text = re.sub(pattern, replacement, text)
    if text != original:
        path.write_text(text, encoding="utf-8")
        extra.append(str(path))

if extra:
    print(f"\nline counts updated in {len(extra)} document(s):")
    for name in extra:
        print(f"  {name}")

print(
    "\nNote: counting lines with PowerShell's `Get-Content | Measure-Object -Line` "
    "undercounts these files. This script is the authoritative source."
)
