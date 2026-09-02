from __future__ import annotations

import argparse
import json

from forge.config import Config
from forge.db import init_db
from forge.health import capabilities, report
from forge.storage import ensure_identity, ensure_layout


def main() -> int:
    parser = argparse.ArgumentParser(prog="ooc-forge")
    parser.add_argument("command", choices=("init", "doctor", "identity"))
    args = parser.parse_args()
    config = Config.load()
    ensure_layout(config)
    init_db(config)
    if args.command == "init":
        print(config.data_root)
    elif args.command == "identity":
        print(json.dumps(ensure_identity(config), indent=2, sort_keys=True))
    elif args.command == "doctor":
        print(
            json.dumps(
                {"health": report(config), "capabilities": capabilities(config)},
                indent=2,
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
