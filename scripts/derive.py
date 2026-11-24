"""Recompute derived colors from their rules and update their hex values in palette.toml.

    python3 scripts/derive.py           # rewrite stale hex values in place
    python3 scripts/derive.py --check   # list stale values; change nothing (exit 1 if any)

Only the `hex = "#......"` part of each derived color's line changes; comments
and layout are left alone.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib

import rhino

HEADER_RE = re.compile(r"^\s*\[([\w.]+)\]")
HEX_FIELD_RE = re.compile(r'(\bhex\s*=\s*")#[0-9A-Fa-f]{6}(")')


def replace_hex(text: str, section: str, key: str, new: str) -> str:
    """Set the `hex` of `key` inside `[section]`."""
    out, current, hits = [], None, 0
    key_re = re.compile(rf"^\s*{re.escape(key)}\s*=")
    for line in text.splitlines(keepends=True):
        if m := HEADER_RE.match(line):
            current = m.group(1)
        elif current == section and key_re.match(line):
            line, n = HEX_FIELD_RE.subn(rf"\g<1>{new}\g<2>", line)
            hits += n
        out.append(line)
    if hits != 1:
        raise SystemExit(f"derive.py: expected one hex for {key} in [{section}], found {hits}")
    return "".join(out)


def location(name: str, appearance: str) -> tuple[str, str]:
    """("readable.red", "light") -> ("light.colors.readable", "red")."""
    table, key = name.rsplit(".", 1)
    prefix = "" if appearance == "dark" else f"{appearance}."
    return f"{prefix}colors.{table}", key


def main() -> int:
    parser = argparse.ArgumentParser(description="Recompute derived colors and update palette.toml.")
    parser.add_argument("--check", action="store_true", help="list stale values without changing anything")
    args = parser.parse_args()

    original = text = rhino.PALETTE_PATH.read_text(encoding="utf-8")
    changes: dict[str, tuple[str, str]] = {}
    # A rule can read another derived color, so repeat until nothing moves.
    for _ in range(5):
        data = tomllib.loads(text)
        found = [(a, *item) for a in rhino.appearances(data) for item in rhino.stale(data, a)]
        if not found:
            break
        if args.check:
            for appearance, name, old, new in found:
                print(f"stale ({appearance}): {name} is {old}, its rule gives {new}")
            return 1
        for appearance, name, old, new in found:
            text = replace_hex(text, *location(name, appearance), new)
            label = name if appearance == "dark" else f"{appearance}: {name}"
            changes[label] = (changes.get(label, (old, new))[0], new)
    else:
        raise SystemExit("derive.py: rules did not settle after 5 passes")

    if text == original:
        print("All derived colors are up to date.")
        return 0
    rhino.PALETTE_PATH.write_text(text, encoding="utf-8")
    for name, (old, new) in changes.items():
        print(f"updated {name}: {old} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
