#!/usr/bin/env python3
"""check_pr_title: gate a PR title against the CON-11 conventional-commit
form BEFORE squash-merge turns it into a mainline subject (CON-8 CLI: JSON
to stdout, exit 0 iff ok).

The pattern here is the single source of truth; the CON-11 mainline
history test imports it.
"""

import json
import re
import sys

CONVENTIONAL = re.compile(r"^(feat|fix|test|spec|chore|docs|refactor|perf|ci)(\([^)]+\))?!?: .+")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(json.dumps({"ok": False, "error": "usage: check_pr_title.py <title>"}))
        return 1
    title = argv[0]
    ok = bool(CONVENTIONAL.match(title))
    report = {"ok": ok, "title": title}
    if not ok:
        report["error"] = (
            "PR title is not a conventional-commit subject (CON-11); squash-merge "
            "will make it a mainline commit. Use e.g. 'feat: ...', 'fix: ...'"
        )
    print(json.dumps(report))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
