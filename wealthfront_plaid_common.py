from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from plaid import ApiClient, Configuration
from plaid.api.plaid_api import PlaidApi
from plaid.exceptions import ApiException
from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest
from plaid.model.investments_transactions_get_request import InvestmentsTransactionsGetRequest
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.transactions_sync_request import TransactionsSyncRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions
from plaid.model.country_code import CountryCode
from plaid.model.products import Products


ROOT = Path(__file__).resolve().parent
CONFIG_FILE = Path.home() / ".finance_lens_plaid.json"
DEFAULT_CACHE_FILE = ROOT / "wealthfront-cache.json"
if os.environ.get("PLAID_CACHE_OVERRIDE"):
    DEFAULT_CACHE_FILE = Path(os.environ["PLAID_CACHE_OVERRIDE"]).expanduser()
DEFAULT_CLIENT_NAME = "Finance Lens"
DEFAULT_LANGUAGE = "en"
DEFAULT_COUNTRY = "US"
DEFAULT_PRODUCTS = ["transactions", "investments"]
DEFAULT_ENV = "development"
PLAID_VERSION = "2020-09-14"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def load_state() -> dict[str, Any]:
    state = load_json(CONFIG_FILE, {})
    if not isinstance(state, dict):
        return {}
    return state


def save_state(state: dict[str, Any]) -> None:
    save_json(CONFIG_FILE, state)


def reset_plaid_link(state: dict[str, Any] | None = None) -> None:
    """Clears access token + cursor so the next Link flow starts completely fresh.
    Useful when you get 'cursor not associated with access_token' errors.
    """
    state = ensure_state_defaults(state or load_state())
    for key in ("access_token", "item_id", "transactions_cursor", "linked_at", "last_sync"):
        state.pop(key, None)
    save_state(state)


def ensure_state_defaults(state: dict[str, Any]) -> dict[str, Any]:
    merged = dict(state)
    merged.setdefault("client_name", DEFAULT_CLIENT_NAME)
    merged.setdefault("language", DEFAULT_LANGUAGE)
    merged.setdefault("country_code", DEFAULT_COUNTRY)
    merged.setdefault("products", list(DEFAULT_PRODUCTS))
    merged.setdefault("env", DEFAULT_ENV)
    merged.setdefault("cache_path", str(DEFAULT_CACHE_FILE))
    return merged


def _plaid_host(env: str) -> str:
    env = (env or "").strip().lower()
    if env == "sandbox":
        return "https://sandbox.plaid.com"
    if env == "production":
        return "https://production.plaid.com"
    return "https://development.plaid.com"


def plaid_client(state: dict[str, Any] | None = None) -> PlaidApi:
    state = ensure_state_defaults(state or load_state())
    client_id = str(state.get("client_id") or "").strip()
    secret = str(state.get("secret") or "").strip()
    if not client_id or not secret:
        raise RuntimeError("Plaid client_id and secret must be saved first.")

    configuration = Configuration(
        host=_plaid_host(str(state.get("env") or DEFAULT_ENV)),
        api_key={
            "clientId": client_id,
            "secret": secret,
            "plaidVersion": PLAID_VERSION,
        },
    )
    return PlaidApi(ApiClient(configuration))


def create_link_token(state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = ensure_state_defaults(state or load_state())
    client = plaid_client(state)
    user_id = str(state.get("user_id") or uuid.uuid4().hex)
    state["user_id"] = user_id
    save_state(state)

    request = LinkTokenCreateRequest(
        client_name=str(state.get("client_name") or DEFAULT_CLIENT_NAME),
        language=str(state.get("language") or DEFAULT_LANGUAGE),
        country_codes=[CountryCode(str(state.get("country_code") or DEFAULT_COUNTRY))],
        products=[Products(p) for p in (state.get("products") or DEFAULT_PRODUCTS)],
        user=LinkTokenCreateRequestUser(user_id),
    )
    response = client.link_token_create(request)
    return response.to_dict()


def exchange_public_token(public_token: str, state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = ensure_state_defaults(state or load_state())
    client = plaid_client(state)
    response = client.item_public_token_exchange(ItemPublicTokenExchangeRequest(public_token))
    payload = response.to_dict()
    state["access_token"] = payload.get("access_token")
    if payload.get("item_id"):
        state["item_id"] = payload["item_id"]
    state["linked_at"] = _now_iso()

    # CRITICAL: When you get a *new* access_token (re-link or new item),
    # any previous transactions_cursor is invalid for this token.
    # Plaid will return: "cursor not associated with access_token"
    state.pop("transactions_cursor", None)

    save_state(state)
    return payload


def _serialize(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if hasattr(value, "to_dict"):
        return _serialize(value.to_dict())
    if hasattr(value, "__dict__"):
        return _serialize(value.__dict__)
    return str(value)


def sync_wealthfront(state: dict[str, Any] | None = None, cache_path: Path | None = None) -> dict[str, Any]:
    state = ensure_state_defaults(state or load_state())
    access_token = str(state.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("No Plaid access token is stored yet. Connect Wealthfront first.")

    client = plaid_client(state)
    cursor = str(state.get("transactions_cursor") or "").strip() or None
    added: list[dict[str, Any]] = []
    modified: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []

    # Retry loop: if we get "cursor not associated with access_token",
    # clear the cursor and start a fresh sync. This can happen after re-linking.
    for attempt in range(2):
        current_cursor = cursor
        added.clear()
        modified.clear()
        removed.clear()
        try:
            while True:
                request = TransactionsSyncRequest(access_token=access_token, cursor=current_cursor) if current_cursor else TransactionsSyncRequest(access_token=access_token)
                response = client.transactions_sync(request).to_dict()
                added.extend(_serialize(response.get("added", [])))
                modified.extend(_serialize(response.get("modified", [])))
                removed.extend(_serialize(response.get("removed", [])))
                current_cursor = response.get("next_cursor") or current_cursor
                if not response.get("has_more"):
                    cursor = current_cursor
                    break
            break  # success
        except ApiException as e:
            body = getattr(e, "body", None) or str(e)
            body_str = str(body).lower() if body else str(e).lower()
            if "cursor not associated with access_token" in body_str and cursor is not None and attempt == 0:
                print("[plaid] Stale cursor detected for current access_token. Clearing cursor and starting fresh sync...")
                cursor = None
                state["transactions_cursor"] = None
                save_state(state)
                continue
            raise
        except Exception as e:
            # Some errors may come wrapped; check message
            if "cursor not associated with access_token" in str(e).lower() and cursor is not None and attempt == 0:
                print("[plaid] Stale cursor (generic). Clearing and retrying fresh...")
                cursor = None
                state["transactions_cursor"] = None
                save_state(state)
                continue
            raise

    today = date.today()
    start_date = today - timedelta(days=365)

    # Fallback to transactions_get if transactions_sync returned no data (common for some accounts after link)
    if len(added) == 0:
        try:
            print("[plaid] transactions_sync returned no data, falling back to transactions_get for historical cash flow...")
            get_request = TransactionsGetRequest(
                access_token=access_token,
                start_date=start_date,
                end_date=today,
                options=TransactionsGetRequestOptions(count=500)
            )
            get_response = client.transactions_get(get_request).to_dict()
            added = _serialize(get_response.get("transactions", []))
            # For fallback, we treat all as "added" for the cache
        except Exception as e:
            print("[plaid] Fallback transactions_get also failed:", str(e))

    investments_holdings = client.investments_holdings_get(InvestmentsHoldingsGetRequest(access_token)).to_dict()
    investments_transactions = client.investments_transactions_get(
        InvestmentsTransactionsGetRequest(
            access_token=access_token,
            start_date=start_date,
            end_date=today
        )
    ).to_dict()

    cache = {
        "source": "plaid",
        "institution": "wealthfront",
        "updated_at": _now_iso(),
        "account_label": "Wealthfront",
        "accounts": _serialize(investments_holdings.get("accounts", [])),
        "holdings": _serialize(investments_holdings.get("holdings", [])),
        "securities": _serialize(investments_holdings.get("securities", [])),
        "investment_transactions": _serialize(investments_transactions.get("investment_transactions", [])),
        "cash_transactions": {
            "added": added,
            "modified": modified,
            "removed": removed,
            "next_cursor": cursor,
            "count": len(added),
        },
        "summary": {
            "accounts": len(investments_holdings.get("accounts", []) or []),
            "holdings": len(investments_holdings.get("holdings", []) or []),
            "securities": len(investments_holdings.get("securities", []) or []),
            "investment_transactions": len(investments_transactions.get("investment_transactions", []) or []),
            "cash_added": len(added),
            "cash_modified": len(modified),
            "cash_removed": len(removed),
        },
    }

    cache_file = cache_path or Path(str(state.get("cache_path") or DEFAULT_CACHE_FILE))
    cache_file = cache_file.expanduser()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    save_json(cache_file, cache)

    state["transactions_cursor"] = cursor
    state["last_sync"] = cache["updated_at"]
    state["cache_path"] = str(cache_file)
    save_state(state)
    return cache
