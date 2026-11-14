"""Benchmark test verifying p95 < 500ms over 100 sequential webhook requests."""

import json
import time
from unittest.mock import AsyncMock

import pytest

from procure_core.config import Settings
from procure_ingress.adapters.slack import SlackIngressAdapter, verify_slack_signature
from tests.unit.test_slack_adapter import generate_slack_headers, make_mock_request


@pytest.mark.unit
async def test_ingress_sequential_latency_p95() -> None:
    """Benchmark 100 sequential webhook requests to verify p95 latency < 500ms."""
    settings = Settings(SLACK_SIGNING_SECRET="benchmark_secret")
    adapter = SlackIngressAdapter()
    latencies: list[float] = []

    for i in range(100):
        body_dict = {
            "type": "event_callback",
            "event_id": f"Ev_bench_{i:04d}",
            "team_id": "T12345",
            "event": {
                "type": "app_mention",
                "user": "U12345",
                "text": "<@U999> Requesting 5 new laptops for engineering team",
                "ts": f"1710000000.{i:06d}",
                "channel": "C12345",
            },
        }
        body_bytes = json.dumps(body_dict).encode("utf-8")
        headers = generate_slack_headers(body_bytes, settings.SLACK_SIGNING_SECRET)
        req = make_mock_request(headers, body_bytes)

        start = time.perf_counter()

        # 1. Verify signature
        verified_body = await verify_slack_signature(req, settings.SLACK_SIGNING_SECRET)
        parsed = json.loads(verified_body)

        # 2. Filter check
        assert not adapter.should_ignore(parsed)

        # 3. Normalize
        normalized = adapter.normalize(parsed, tenant_id=settings.DEFAULT_TENANT_ID)
        dedup_key = adapter.extract_dedup_key(parsed)
        ordering_key = adapter.extract_ordering_key(parsed)

        # 4. Mock queue enqueue operation
        mock_queue = AsyncMock()
        mock_queue.enqueue = AsyncMock(return_value=True)
        await mock_queue.enqueue(
            tenant_id=normalized.tenant_id,
            channel="slack",
            event_id=dedup_key,
            ordering_key=ordering_key,
            payload=parsed,
            normalized_payload=normalized.model_dump(mode="json"),
        )

        duration = time.perf_counter() - start
        latencies.append(duration)

    latencies.sort()
    p95_index = int(len(latencies) * 0.95)
    p95_latency = latencies[p95_index]
    avg_latency = sum(latencies) / len(latencies)

    # Acceptance requirement: p95 < 500ms (0.500 seconds)
    assert p95_latency < 0.500, f"p95 latency was {p95_latency * 1000:.2f}ms (threshold: 500ms)"
    assert avg_latency < 0.050, f"Average latency was {avg_latency * 1000:.2f}ms"
