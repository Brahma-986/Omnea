"""Standalone HTTP Webhook Receiver with HMAC-SHA256 signature and replay verification."""

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

DEFAULT_WEBHOOK_SECRET = "whsec_super_secret_signing_key_for_omnea"


class OmneaWebhookHandler(BaseHTTPRequestHandler):
    receiver = WebhookReceiver(secret=DEFAULT_WEBHOOK_SECRET, tolerance=300)

    def do_POST(self) -> None:  # noqa: N802
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        sig_header = self.headers.get("X-Omnea-Signature", "")
        event_type = self.headers.get("X-Omnea-Event", "unknown")
        delivery_id = self.headers.get("X-Omnea-Delivery", "unknown")

        print(f"\n[Webhook Received] Event: {event_type} | Delivery: {delivery_id}")

        # 1. Verify HMAC-SHA256 signature & replay window
        is_valid = self.receiver.verify_signature(
            payload_bytes=body,
            signature_header=sig_header,
        )

        if not is_valid:
            print("[SECURITY ALERT] Invalid signature or replayed webhook!")
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "Invalid HMAC signature or replayed timestamp"}')
            return

        # 2. Parse verified payload
        try:
            payload = json.loads(body.decode("utf-8"))
            print(f"[VERIFIED AUTHENTIC] Payload: {json.dumps(payload, indent=2)}")
        except Exception as e:
            print(f"[ERROR] Could not parse JSON body: {e}")

        # 3. Respond 200 OK
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "received", "verified": true}')

    def log_message(self, format: str, *args: object) -> None:
        pass


def run_server(port: int = 8088, secret: str = DEFAULT_WEBHOOK_SECRET) -> None:
    OmneaWebhookHandler.receiver = WebhookReceiver(secret=secret)
    server_address = ("127.0.0.1", port)
    httpd = HTTPServer(server_address, OmneaWebhookHandler)
    print(f"Omnea Webhook Receiver listening on http://127.0.0.1:{port} (Secret: {secret[:12]}...)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping webhook receiver server.")
        httpd.server_close()


if __name__ == "__main__":
    port_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 8088
    run_server(port=port_arg)
