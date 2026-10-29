import argparse
import json
from pathlib import Path
import sys
import httpx


def send_messages(api_url: str, file_path: Path) -> None:
    if not file_path.exists():
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    with open(file_path, "r", encoding="utf-8") as f:
        messages = json.load(f)

    print(f"Loaded {len(messages)} messages from {file_path}")
    print(f"Posting to: {api_url}/messages\n")

    accepted = 0
    duplicates = 0
    rejected = 0
    errors = 0

    with httpx.Client(timeout=10.0) as client:
        for idx, msg in enumerate(messages, 1):
            mid = msg.get("message_id", f"unknown-{idx}")
            try:
                resp = client.post(f"{api_url}/messages", json=msg)
                if resp.status_code in (200, 202):
                    data = resp.json()
                    status = data.get("status")
                    if status == "accepted":
                        accepted += 1
                        print(f"[{idx:02d}] {mid} -> ACCEPTED (202)")
                    elif status == "duplicate":
                        duplicates += 1
                        print(f"[{idx:02d}] {mid} -> DUPLICATE (200) - {data.get('details')}")
                    elif status == "rejected":
                        rejected += 1
                        print(f"[{idx:02d}] {mid} -> REJECTED (200) - {data.get('details')}")
                else:
                    errors += 1
                    print(f"[{idx:02d}] {mid} -> ERROR {resp.status_code}: {resp.text}")
            except Exception as e:
                errors += 1
                print(f"[{idx:02d}] {mid} -> EXCEPTION: {e}")

    print("\n--- Summary ---")
    print(f"Total processed: {len(messages)}")
    print(f"Accepted:        {accepted}")
    print(f"Duplicates:      {duplicates}")
    print(f"Rejected:        {rejected}")
    print(f"Errors:          {errors}")


def main():
    parser = argparse.ArgumentParser(description="Send message batches to Ticket Intelligence API")
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8000",
        help="FastAPI base URL (default: http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--file",
        default=str(Path(__file__).resolve().parents[2] / "for_candidates" / "practice_messages.json"),
        help="Path to JSON messages file",
    )
    args = parser.parse_args()
    send_messages(args.url.rstrip("/"), Path(args.file))


if __name__ == "__main__":
    main()

