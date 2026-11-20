"""End-to-End Acceptance Example: Calls API with scoped key, creates request, and verifies signed webhook."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Add Python SDK to path
SDK_PATH = Path(__file__).resolve().parent.parent / "sdk" / "python"
if str(SDK_PATH) not in sys.path:
    sys.path.insert(0, str(SDK_PATH))

from omnea.client import OmneaClient  # noqa: E402
from omnea.webhook import WebhookReceiver  # noqa: E402

WEBHOOK_SECRET = "whsec_acceptance_test_secret_key_12345678"
SERVER_PORT = 8999

received_events: list[dict] = []
verification_results: list[bool] = []
webhook_received_event = threading.Event()


class AcceptanceWebhookHandler(BaseHTTPRequestHandler):
    receiver = WebhookReceiver(secret=WEBHOOK_SECRET, tolerance=300)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        sig = self.headers.get("X-Omnea-Signature", "")
        event_type = self.headers.get("X-Omnea-Event", "")

        # Verify HMAC-SHA256 signature using SDK WebhookReceiver
        is_valid = self.receiver.verify_signature(
            payload_bytes=body,
            signature_header=sig,
        )

        verification_results.append(is_valid)
        try:
            payload = json.loads(body.decode("utf-8"))
            received_events.append({"event": event_type, "payload": payload, "valid": is_valid})
        except Exception:
            pass

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')
        webhook_received_event.set()

    def log_message(self, format: str, *args: object) -> None:
        pass


def start_receiver_server() -> HTTPServer:
    httpd = HTTPServer(("127.0.0.1", SERVER_PORT), AcceptanceWebhookHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def run_acceptance_example(api_key: str, base_url: str = "http://127.0.0.1:8000") -> bool:
    print("=" * 70)
    print("OMNEA STAGE 7 ACCEPTANCE: End-to-End API Key & Webhook Verification")
    print("=" * 70)

    # 1. Start local webhook receiver server
    httpd = start_receiver_server()
    webhook_url = f"http://127.0.0.1:{SERVER_PORT}/webhook"
    print(f"[1] Started local Webhook Receiver at {webhook_url}")

    # 2. Initialize OmneaClient with scoped API key
    client = OmneaClient(api_key=api_key, base_url=base_url)
    print(f"[2] Initialized OmneaClient with API key prefix: {api_key[:18]}...")

    # 3. Register outbound webhook endpoint with signing secret
    print("[3] Registering webhook endpoint subscribed to 'request.created'...")
    endpoint = client.webhooks.create_endpoint(
        url=webhook_url,
        events=["request.created"],
        description="End-to-End Acceptance Webhook Endpoint",
        secret=WEBHOOK_SECRET,
    )
    print(f"    Registered Endpoint ID: {endpoint['id']}")

    # 4. Submit a procurement request using scoped API key
    print("[4] Submitting procurement request via client.requests.create()...")
    created_req = client.requests.create(
        vendor_name="Datadog, Inc.",
        estimated_spend=48000.00,
        currency="USD",
        department="Engineering",
        description="Datadog Observability annual contract renewal",
        justification="Critical production infrastructure monitoring",
    )
    request_id = created_req["id"]
    print(f"    Created Request ID: {request_id}")
    print(f"    Status:             {created_req['status']}")

    # 5. Wait for signed webhook to be received and verified
    print("[5] Waiting for HMAC-SHA256 signed webhook delivery...")
    received = webhook_received_event.wait(timeout=10.0)

    httpd.shutdown()

    if not received:
        print("[FAIL] Webhook was not received within timeout!")
        return False

    if not verification_results or not verification_results[-1]:
        print("[FAIL] Webhook received but HMAC-SHA256 signature verification FAILED!")
        return False

    delivered_event = received_events[-1]
    payload = delivered_event["payload"]
    delivered_req_id = payload.get("request_id")

    print("[SUCCESS] Webhook received and verified authentic!")
    print(f"    Event:            {delivered_event['event']}")
    print(f"    Signature Valid:  {delivered_event['valid']}")
    print(f"    Payload Req ID:   {delivered_req_id}")
    print(f"    Vendor Name:      {payload.get('vendor_name')}")
    print(f"    Estimated Spend:  {payload.get('estimated_spend')} {payload.get('currency')}")

    assert delivered_req_id == request_id, (
        f"Mismatched request ID: {delivered_req_id} != {request_id}"
    )
    print("=" * 70)
    print("STAGE 7 ACCEPTANCE CRITERIA VERIFIED: API Key + Webhook End-to-End PASS")
    print("=" * 70)
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python end_to_end_example.py <omnea_live_api_key> [base_url]")
        sys.exit(1)
    k = sys.argv[1]
    u = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8000"
    success = run_acceptance_example(api_key=k, base_url=u)
    sys.exit(0 if success else 1)
