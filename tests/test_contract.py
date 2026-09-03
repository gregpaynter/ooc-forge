from __future__ import annotations

import json
from pathlib import Path


def test_reliability_contract_is_versioned_and_complete():
    contract = json.loads(
        (
            Path(__file__).parents[1] / "contracts" / "forge-reliability-v1.json"
        ).read_text()
    )
    assert contract["schema"] == "ooc.forge-reliability.v1"
    assert "attempt_token" in contract["claim"]["response_job_required"]
    assert "client_asset_id" in contract["asset"]["multipart_required"]
    assert contract["rules"]["persist_claim_before_execute"] is True
    assert contract["rules"]["all_assets_must_be_verified_before_completion"] is True
