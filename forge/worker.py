from __future__ import annotations

import json
import time

from forge.config import Config
from forge.db import claim_local_job, fail_job, finish_job, init_db, record_assets
from forge.executor import execute
from forge.storage import ensure_layout


def run_once(config: Config) -> bool:
    row = claim_local_job(config)
    if not row:
        return False
    job_id = str(row["id"])
    try:
        request = json.loads(str(row["request_json"]))
        result = execute(request, local_job_id=job_id)
        record_assets(config, job_id, list(result.get("assets") or []))
        finish_job(config, job_id, result)
    except Exception as error:
        fail_job(config, job_id, str(error))
    return True


def main() -> int:
    config = Config.load()
    ensure_layout(config)
    init_db(config)
    while True:
        if not run_once(config):
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
