"""Slack bot integration example listening for Omnea webhook events and posting notifications."""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# Add Python SDK to path
SDK_PATH = Path(__file__).resolve().parent.parent / "sdk" / "python"
if str(SDK_PATH) not in sys.path:
    sys.path.insert(0, str(SDK_PATH))

from omnea.webhook import WebhookReceiver  # noqa: E402


class SlackBotWebhookHandler(BaseHTTPRequestHandler):
    receiver = WebhookReceiver(secret="whsec_slack_bot_secret_12345")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        sig = self.headers.get("X-Omnea-Signature", "")
        event = self.headers.get("X-Omnea-Event", "")

        # Verify HMAC-SHA256 signature
        if not self.receiver.verify_signature(payload_bytes=body, signature_header=sig):
            self.send_response(401)
            self.end_headers()
            return

        payload = json.loads(body.decode("utf-8"))
        print(f"\n[Slack Bot] Received verified event: {event}")

        # Format interactive message blocks
        if event == "request.created":
            req_id = payload.get("request_id")
            vendor = payload.get("vendor_name")
            spend = payload.get("estimated_spend")
            curr = payload.get("currency", "USD")
            print(
                f"--> Posting to #procurement-approvals: New request {req_id} for {vendor} ({spend} {curr})"
            )
            print("--> [Button: Approve]  [Button: Reject]  [Button: Inspect Evidence]")

        elif event == "request.approved":
            req_id = payload.get("request_id")
            decided_by = payload.get("decided_by")
            print(
                f"--> Updating message: Request {req_id} APPROVED by {decided_by} (PO generation started)"
            )

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format: str, *args: object) -> None:
        pass


def run_slack_bot(port: int = 9090) -> None:
    server = HTTPServer(("127.0.0.1", port), SlackBotWebhookHandler)
    print(f"Slack Bot Webhook Server listening on http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run_slack_bot()
