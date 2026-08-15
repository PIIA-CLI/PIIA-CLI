#!/usr/bin/env python3
"""Publish the CLI's generated JSON Schemas, or verify they have not drifted."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from piia.output.schemas import DATA_SCHEMAS, all_schemas, envelope_schema  # noqa: E402


def rendered_schemas() -> dict[Path, str]:
    output = ROOT / "schemas" / "v1"
    payloads = {
        output / "cli-envelope.json": envelope_schema(),
        output / "all.json": all_schemas(),
    }
    payloads.update(
        {
            output / f"{command.replace(' ', '-')}.json": envelope_schema(command)
            for command in DATA_SCHEMAS
        }
    )
    return {path: json.dumps(payload, indent=2) + "\n" for path, payload in payloads.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check", action="store_true", help="Fail if committed schemas differ from generated ones."
    )
    args = parser.parse_args()

    stale: list[str] = []
    for path, expected in rendered_schemas().items():
        if args.check:
            if not path.is_file() or path.read_text(encoding="utf-8") != expected:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(expected, encoding="utf-8")

    if stale:
        print("Schema files are stale: " + ", ".join(stale), file=sys.stderr)
        print("Run: python scripts/export_schemas.py", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
