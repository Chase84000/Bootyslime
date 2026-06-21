from __future__ import annotations

import argparse
from pathlib import Path

from wealthfront_plaid_common import DEFAULT_CACHE_FILE, load_state, sync_wealthfront


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Wealthfront data through Plaid into a local cache.")
    parser.add_argument("--cache", dest="cache", default=None, help="Optional cache path override.")
    args = parser.parse_args()

    cache_path = Path(args.cache).expanduser() if args.cache else DEFAULT_CACHE_FILE
    cache = sync_wealthfront(state=load_state(), cache_path=cache_path)
    summary = cache.get("summary", {})
    print(
        f"Synced Wealthfront cache: {summary.get('accounts', 0)} accounts, "
        f"{summary.get('holdings', 0)} holdings, "
        f"{summary.get('investment_transactions', 0)} investment transactions."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
