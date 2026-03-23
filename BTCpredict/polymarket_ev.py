# polymarket_ev.py
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple, List

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "pm-ev-dashboard/slug/1.0"})


def _safe_json(x: Any) -> Any:
    if isinstance(x, str):
        s = x.strip()
        if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
            try:
                return json.loads(s)
            except Exception:
                return x
    return x


def normalize_slug(user_input: str) -> str:
    """
    Accepts:
      - btc-updown-15m-1771318800
      - https://polymarket.com/event/btc-updown-15m-1771318800
      - https://polymarket.com/market/btc-updown-15m-1771318800
    Returns just the slug.
    """
    if not user_input:
        return ""
    s = str(user_input).strip()

    # If it's a URL, extract last path segment that looks like a slug
    if "http://" in s or "https://" in s:
        # grab last token after /
        s = s.rstrip("/")
        s = s.split("/")[-1]

    # cleanup accidental querystrings
    s = s.split("?")[0].split("#")[0].strip()
    return s


def gamma_market_by_slug(slug_or_url: str) -> Optional[Dict[str, Any]]:
    """
    Fetch a specific market from Gamma by slug.
    We try multiple approaches because Gamma deployments vary.
    """
    slug = normalize_slug(slug_or_url)
    if not slug:
        return None

    # 1) Try direct filter (?slug=)
    try:
        r = _SESSION.get(f"{GAMMA}/markets", params={"slug": slug, "limit": "50", "offset": "0"}, timeout=10)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            # exact match if present
            for m in data:
                if str(m.get("slug")) == slug:
                    m["clobTokenIds"] = _safe_json(m.get("clobTokenIds"))
                    m["outcomes"] = _safe_json(m.get("outcomes"))
                    return m
            # else return first
            m = data[0]
            m["clobTokenIds"] = _safe_json(m.get("clobTokenIds"))
            m["outcomes"] = _safe_json(m.get("outcomes"))
            return m
    except Exception:
        pass

    # 2) Try search=slug
    try:
        r = _SESSION.get(
            f"{GAMMA}/markets",
            params={"search": slug, "closed": "false", "limit": "50", "offset": "0"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and data:
            for m in data:
                if str(m.get("slug")) == slug:
                    m["clobTokenIds"] = _safe_json(m.get("clobTokenIds"))
                    m["outcomes"] = _safe_json(m.get("outcomes"))
                    return m
    except Exception:
        pass

    return None


def clob_best_ask(token_id: str) -> Optional[float]:
    """
    Best ask to BUY = side=BUY
    """
    if not token_id:
        return None
    r = _SESSION.get(f"{CLOB}/price", params={"token_id": token_id, "side": "BUY"}, timeout=10)
    r.raise_for_status()
    j = r.json()
    try:
        return float(j.get("price"))
    except Exception:
        return None


def get_up_down_prices(market: Dict[str, Any]) -> Optional[Tuple[float, float, Dict[str, Any]]]:
    """
    Returns (up_ask, down_ask, debug)
    """
    if not market:
        return None

    token_ids = _safe_json(market.get("clobTokenIds"))
    outcomes = _safe_json(market.get("outcomes"))

    if not isinstance(token_ids, list) or len(token_ids) < 2:
        return None

    up_idx, down_idx = 0, 1
    if isinstance(outcomes, list) and len(outcomes) >= 2:
        low = [str(x).lower() for x in outcomes]
        for i, txt in enumerate(low):
            if "up" in txt:
                up_idx = i
            if "down" in txt:
                down_idx = i

    up_token = str(token_ids[up_idx])
    down_token = str(token_ids[down_idx])

    up = clob_best_ask(up_token)
    down = clob_best_ask(down_token)
    if up is None or down is None:
        return None

    dbg = {
        "slug": market.get("slug"),
        "outcomes": outcomes,
        "up_token": up_token,
        "down_token": down_token,
    }
    return up, down, dbg


