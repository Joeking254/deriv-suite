import json
import os
from pathlib import Path
from typing import Dict, Tuple


ROOT = Path(__file__).resolve().parents[1]


def _resolve_path(value: str) -> str:
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((ROOT / path).resolve())


def _default_path() -> str:
    configured = os.getenv("TOKEN_STORE_PATH", "").strip()
    if configured:
        return _resolve_path(configured)
    return str((Path(__file__).resolve().parent / "tokens.json").resolve())


def _normalize_mode(value: str) -> str:
    return "live" if value and value.lower().strip() == "live" else "demo"


def load_tokens(path: str | None = None) -> Dict[str, str]:
    token_path = _resolve_path(path) if path else _default_path()
    if not os.path.exists(token_path):
        return {"active_mode": "demo", "demo_token": "", "live_token": ""}
    try:
        with open(token_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return {"active_mode": "demo", "demo_token": "", "live_token": ""}
    return {
        "active_mode": _normalize_mode(data.get("active_mode", "demo")),
        "demo_token": str(data.get("demo_token", "")),
        "live_token": str(data.get("live_token", "")),
    }


def save_tokens(data: Dict[str, str], path: str | None = None) -> None:
    token_path = _resolve_path(path) if path else _default_path()
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    payload = {
        "active_mode": _normalize_mode(data.get("active_mode", "demo")),
        "demo_token": data.get("demo_token", ""),
        "live_token": data.get("live_token", ""),
    }
    with open(token_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def resolve_token(
    api_token: str | None,
    account_mode: str,
    token_store_path: str | None = None,
) -> Tuple[str | None, str]:
    tokens = load_tokens(token_store_path)
    active_mode = tokens.get("active_mode") or _normalize_mode(account_mode)
    active_mode = _normalize_mode(active_mode)

    token_key = "demo_token" if active_mode == "demo" else "live_token"
    token = tokens.get(token_key, "").strip()
    if token:
        return token, active_mode

    if api_token:
        return api_token.strip(), _normalize_mode(account_mode)

    return None, active_mode


def update_tokens(
    active_mode: str | None = None,
    demo_token: str | None = None,
    live_token: str | None = None,
    token_store_path: str | None = None,
) -> Dict[str, str]:
    tokens = load_tokens(token_store_path)
    if active_mode:
        tokens["active_mode"] = _normalize_mode(active_mode)
    if demo_token:
        tokens["demo_token"] = demo_token.strip()
    if live_token:
        tokens["live_token"] = live_token.strip()
    save_tokens(tokens, token_store_path)
    return tokens
