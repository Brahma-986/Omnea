"""Command-line interface application demonstrating Omnea Python SDK usage."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add Python SDK to path
SDK_PATH = Path(__file__).resolve().parent.parent / "sdk" / "python"
if str(SDK_PATH) not in sys.path:
    sys.path.insert(0, str(SDK_PATH))

from omnea.client import OmneaClient  # noqa: E402
from omnea.exceptions import OmneaError  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Omnea Procurement CLI Example")
    parser.add_argument("--api-key", required=True, help="Scoped Omnea API Key (omnea_live_...)")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Base URL of Omnea API")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Command: create-request
    cr = subparsers.add_parser("create-request", help="Submit a new procurement request")
    cr.add_argument("--vendor", required=True, help="Vendor name")
    cr.add_argument("--spend", required=True, type=float, help="Estimated spend")
    cr.add_argument("--currency", default="USD", help="Currency code")
    cr.add_argument("--dept", default="Engineering", help="Department")
    cr.add_argument("--desc", default="CLI Submitted Request", help="Description")

    # Command: list-requests
    subparsers.add_parser("list-requests", help="List recent requests")

    # Command: erp-status
    subparsers.add_parser("erp-status", help="Check ERP connector and ledger sync status")

    args = parser.parse_args()

    client = OmneaClient(api_key=args.api_key, base_url=args.base_url)

    try:
        if args.command == "create-request":
            print(
                f"Creating procurement request for {args.vendor} (${args.spend} {args.currency})..."
            )
            result = client.requests.create(
                vendor_name=args.vendor,
                estimated_spend=args.spend,
                currency=args.currency,
                department=args.dept,
                description=args.desc,
            )
            print("SUCCESS! Request Created:")
            print(f"  ID:          {result['id']}")
            print(f"  Status:      {result['status']}")
            print(f"  Vendor:      {result['vendor_name']}")
            print(f"  Spend:       {result['estimated_spend']} {result['currency']}")

        elif args.command == "list-requests":
            print("Fetching recent requests...")
            data = client.requests.list(limit=10)
            items = data.get("items", []) if isinstance(data, dict) else data
            print(f"Found {len(items)} requests:")
            for r in items:
                print(
                    f"  - [{r.get('status')}] {r.get('id')}: {r.get('vendor_name', 'N/A')} (${r.get('estimated_spend', 0)})"
                )

        elif args.command == "erp-status":
            print("Checking ERP System of Record status...")
            status = client.erp.get_status()
            print(f"  Connector:   {status.get('connector_type')}")
            print(f"  Status:      {status.get('connection_status')}")
            print(f"  Latency:     {status.get('latency_ms', 0)}ms")

    except OmneaError as exc:
        print(f"Error ({exc.status_code}): {exc.message}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
