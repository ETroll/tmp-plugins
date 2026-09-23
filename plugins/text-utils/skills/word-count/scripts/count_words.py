#!/usr/bin/env python3
"""Print line/word/character counts for a text file as JSON."""

import json
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: count_words.py <path>", file=sys.stderr)
        return 2

    with open(sys.argv[1], "r", encoding="utf-8") as handle:
        text = handle.read()

    stats = {
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "characters": len(text),
    }
    print(json.dumps(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
