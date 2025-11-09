from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import requests


FIXTURE_PATH = Path("tests/data/opencode/synth_small_forbidden_request.json")
DEFAULT_BASE_URL = "https://synth-backend-dev-docker.onrender.com/api/synth-research"


@pytest.mark.integration
@pytest.mark.xfail(reason="Synth blocks the full OpenCode payload via Cloudflare today", strict=False)
def test_synth_small_full_payload_triggers_cloudflare_block() -> None:
    """Exercise the full payload so we notice when the block is lifted."""
    api_key = os.environ.get("SYNTH_API_KEY")
    if not api_key:
        pytest.skip("SYNTH_API_KEY not set")

    base_url = os.environ.get("SYNTH_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    response = requests.post(
        f"{base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
        timeout=30,
    )

    assert 200 <= response.status_code < 300, response.text
