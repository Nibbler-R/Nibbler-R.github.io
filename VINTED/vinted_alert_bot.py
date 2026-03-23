import asyncio
import os
import random
import re
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

import httpx
from bs4 import BeautifulSoup

# ------------------------------------------------------------
# Vinted alert bot (alert-only, not auto-buy)
# ------------------------------------------------------------
# Fixes included:
# - faster parallel search processing
# - stronger text parsing from card + parent + grandparent
# - better price extraction
# - better size extraction
# - Discord debug output
# - safer alert formatting
# - profit based on item + fee + shipping
# - SQLite speed tweaks
# - easier debugging output
#
# Install:
#   pip install httpx beautifulsoup4
#
# Run:
#   python vinted_alert_bot.py
#
# Put your webhook in an environment variable if possible:
#   PowerShell:
#   $env:DISCORD_WEBHOOK="YOUR_WEBHOOK"
# ------------------------------------------------------------

CONFIG: Dict[str, Any] = {
    "poll_seconds": 45,
    "sleep_jitter_min": -5,
    "sleep_jitter_max": 8,
    "db_path": "vinted_seen.db",
    "estimated_shipping_eur": 5.50,
    "max_cards_per_search": 40,
    "debug_raw_text": False,
    "debug_matches": True,
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "discord_webhook": "https://discord.com/api/webhooks/1480576185506463988/5LLRNWepWNcUym8WeADPQsxRgQtyLZOqyaN9OuGpWBf0F0nJ4Zx1MwKXiNneBcoAsGBB",
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
    "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
    "searches": [
        {
            "name": "Air Max Plus TN",
            "url": "https://www.vinted.fi/catalog?search_text=air%20max%20plus&price_to=35&order=newest_first",
            "must_include": ["air max plus", "tn"],
            "must_not_include": ["kids", "fake", "replica", "broken", "damaged", "torn"],
            "max_price_eur": 35.0,
            "min_profit_eur": 5.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["40", "40.5", "41", "42", "42.5", "43", "44", "45", "46"],
        },
        {
            "name": "New Balance 2002R",
            "url": "https://www.vinted.fi/catalog?search_text=new%20balance%202002r&price_to=45&order=newest_first",
            "must_include": ["2002r", "new balance"],
            "must_not_include": ["kids", "fake", "replica", "broken", "damaged", "torn"],
            "max_price_eur": 45.0,
            "min_profit_eur": 5.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["40", "40.5", "41", "42", "42.5", "43", "44", "45", "46"],
        },
        {
            "name": "Nike SB Force 58",
            "url": "https://www.vinted.fi/catalog?search_text=nike%20sb%20force%2058&price_to=20&order=newest_first",
            "must_include": ["nike", "force 58"],
            "must_not_include": ["kids", "fake", "replica", "broken", "damaged", "torn", "heel drag", "cracked", "separation"],
            "max_price_eur": 20.0,
            "min_profit_eur": 5.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["42", "42.5", "43", "44", "45", "46"],
        },
        {
            "name": "Air Force 1",
            "url": "https://www.vinted.fi/catalog?search_text=air%20force%201&price_to=20&order=newest_first",
            "must_include": ["air force 1"],
            "must_not_include": ["kids", "fake", "replica", "broken", "damaged", "torn", "cracked"],
            "max_price_eur": 20.0,
            "min_profit_eur": 5.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["40", "40.5", "41", "42", "42.5", "43", "44", "45", "46"],
        },
        {
            "name": "Air Max 90",
            "url": "https://www.vinted.fi/catalog?search_text=air%20max%2090&price_to=20&order=newest_first",
            "must_include": ["air max 90"],
            "must_not_include": ["kids", "fake", "replica", "broken", "damaged", "torn"],
            "max_price_eur": 20.0,
            "min_profit_eur": 5.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["40", "40.5", "41", "42", "42.5", "43", "44", "45", "46"],
        },
        {
            "name": "Air Max 95",
            "url": "https://www.vinted.fi/catalog?search_text=air%20max%2095&price_to=25&order=newest_first",
            "must_include": ["air max 95"],
            "must_not_include": ["kids", "fake", "replica", "broken", "damaged", "torn"],
            "max_price_eur": 25.0,
            "min_profit_eur": 5.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["40", "40.5", "41", "42", "42.5", "43", "44", "45", "46"],
        },
        {
            "name": "Air Max 97",
            "url": "https://www.vinted.fi/catalog?search_text=air%20max%2097&price_to=25&order=newest_first",
            "must_include": ["air max 97"],
            "must_not_include": ["kids", "fake", "replica", "broken", "damaged", "torn"],
            "max_price_eur": 25.0,
            "min_profit_eur": 5.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["40", "40.5", "41", "42", "42.5", "43", "44", "45", "46"],
        },
        {
            "name": "Nike Dunk",
            "url": "https://www.vinted.fi/catalog?search_text=nike%20dunk&price_to=40&order=newest_first",
            "must_include": ["dunk"],
            "must_not_include": ["kids", "fake", "replica", "broken", "damaged", "torn"],
            "max_price_eur": 40.0,
            "min_profit_eur": 5.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["40", "40.5", "41", "42", "42.5", "43", "44", "45", "46"],
        },
        {
            "name": "Adidas Samba",
            "url": "https://www.vinted.fi/catalog?search_text=adidas%20samba&price_to=40&order=newest_first",
            "must_include": ["samba"],
            "must_not_include": ["kids", "fake", "replica", "broken", "damaged", "torn"],
            "max_price_eur": 40.0,
            "min_profit_eur": 5.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["40", "40.5", "41", "42", "42.5", "43", "44", "45", "46"],
        },
        {
            "name": "New Balance 550",
            "url": "https://www.vinted.fi/catalog?search_text=new%20balance%20550&price_to=40&order=newest_first",
            "must_include": ["550", "new balance"],
            "must_not_include": ["kids", "fake", "replica", "broken", "damaged", "torn"],
            "max_price_eur": 40.0,
            "min_profit_eur": 5.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["40", "40.5", "41", "42", "42.5", "43", "44", "45", "46"],
        },
        {
            "name": "Levis 501",
            "url": "https://www.vinted.fi/catalog?search_text=levis%20501&price_to=20&order=newest_first",
            "must_include": ["501", "levis"],
            "must_not_include": ["kids", "baby", "fake", "replica"],
            "max_price_eur": 20.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": [],
        },
        {
            "name": "Levis 505",
            "url": "https://www.vinted.fi/catalog?search_text=levis%20505&price_to=20&order=newest_first",
            "must_include": ["505", "levis"],
            "must_not_include": ["kids", "baby", "fake", "replica"],
            "max_price_eur": 20.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": [],
        },
        {
            "name": "Levis 550",
            "url": "https://www.vinted.fi/catalog?search_text=levis%20550&price_to=20&order=newest_first",
            "must_include": ["550", "levis"],
            "must_not_include": ["kids", "baby", "fake", "replica"],
            "max_price_eur": 20.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": [],
        },
        {
            "name": "Levis 560",
            "url": "https://www.vinted.fi/catalog?search_text=levis%20560&price_to=20&order=newest_first",
            "must_include": ["560", "levis"],
            "must_not_include": ["kids", "baby", "fake", "replica"],
            "max_price_eur": 20.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": [],
        },
        {
            "name": "Levis 569",
            "url": "https://www.vinted.fi/catalog?search_text=levis%20569&price_to=20&order=newest_first",
            "must_include": ["569", "levis"],
            "must_not_include": ["kids", "baby", "fake", "replica"],
            "max_price_eur": 20.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": [],
        },
        {
            "name": "Vintage Levis",
            "url": "https://www.vinted.fi/catalog?search_text=vintage%20levis&price_to=25&order=newest_first",
            "must_include": ["levis"],
            "must_not_include": ["kids", "baby", "fake", "replica"],
            "max_price_eur": 25.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": [],
        },
        {
            "name": "Levis Made in USA",
            "url": "https://www.vinted.fi/catalog?search_text=levis%20made%20in%20usa&price_to=30&order=newest_first",
            "must_include": ["levis"],
            "must_not_include": ["kids", "baby", "fake", "replica"],
            "max_price_eur": 30.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": [],
        },
        {
            "name": "Nike Hoodie",
            "url": "https://www.vinted.fi/catalog?search_text=nike%20hoodie&price_to=15&order=newest_first",
            "must_include": ["nike", "hoodie"],
            "must_not_include": ["kids", "134", "140", "152", "fake", "replica"],
            "max_price_eur": 15.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["M", "L", "XL"],
        },
        {
            "name": "Carhartt Hoodie",
            "url": "https://www.vinted.fi/catalog?search_text=carhartt%20hoodie&price_to=30&order=newest_first",
            "must_include": ["carhartt"],
            "must_not_include": ["kids", "fake", "replica"],
            "max_price_eur": 30.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["M", "L", "XL"],
        },
        {
            "name": "Football Jersey",
            "url": "https://www.vinted.fi/catalog?search_text=football%20shirt&price_to=20&order=newest_first",
            "must_include": ["nike", "adidas", "umbro"],
            "must_not_include": ["kids", "fake", "replica"],
            "max_price_eur": 20.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["M", "L", "XL"],
        },
        {
            "name": "Levi Misspelling 501",
            "url": "https://www.vinted.fi/catalog?search_text=levi%20501&price_to=20&order=newest_first",
            "must_include": ["501", "levi"],
            "must_not_include": ["kids", "baby", "fake", "replica"],
            "max_price_eur": 20.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": [],
        },
        {
            "name": "Levies Misspelling",
            "url": "https://www.vinted.fi/catalog?search_text=levies&price_to=20&order=newest_first",
            "must_include": ["levi", "levies", "501", "505", "550", "560", "569"],
            "must_not_include": ["kids", "baby", "fake", "replica"],
            "max_price_eur": 20.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": [],
        },
        {
            "name": "Nik Hoodie Misspelling",
            "url": "https://www.vinted.fi/catalog?search_text=nik%20hoodie&price_to=15&order=newest_first",
            "must_include": ["nik", "hoodie", "nike"],
            "must_not_include": ["kids", "134", "140", "152", "fake", "replica"],
            "max_price_eur": 15.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["M", "L", "XL"],
        },
        {
            "name": "Addidas Misspelling",
            "url": "https://www.vinted.fi/catalog?search_text=addidas&price_to=30&order=newest_first",
            "must_include": ["addidas", "adidas"],
            "must_not_include": ["kids", "fake", "replica", "broken", "damaged", "torn"],
            "max_price_eur": 30.0,
            "min_profit_eur": 0.0,
            "min_score": 1,
            "new_only": False,
            "allowed_sizes": ["40", "40.5", "41", "42", "42.5", "43", "44", "45", "46", "M", "L", "XL"],
        },
    ],
}

GOOD_LEVIS_MODELS: Set[str] = {"501", "505", "550", "560", "569"}
BAD_LEVIS_WORDS: Set[str] = {
    "kids",
    "baby",
    "girls",
    "boys",
    "jeggings",
    "leggings",
    "shorts",
    "dungarees",
    "overall",
    "overalls",
    "pregnancy",
    "maternity",
}
BAD_SHOE_WORDS: Set[str] = {
    "heel drag",
    "heel worn",
    "sole worn",
    "cracked",
    "separation",
    "damaged",
    "broken",
    "torn",
    "fake",
    "replica",
}


@dataclass
class Listing:
    listing_id: str
    title: str
    url: str
    price_eur: Optional[float]
    size: Optional[str]
    brand: Optional[str]
    condition: Optional[str]
    image_url: Optional[str]
    raw_text: str


class SeenStore:
    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen (
                listing_id TEXT PRIMARY KEY,
                first_seen_ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                title TEXT,
                url TEXT
            )
            """
        )
        self.conn.commit()

    def has(self, listing_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM seen WHERE listing_id = ? LIMIT 1",
            (listing_id,),
        )
        return cur.fetchone() is not None

    def add(self, listing: Listing) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen(listing_id, title, url) VALUES (?, ?, ?)",
            (listing.listing_id, listing.title, listing.url),
        )
        self.conn.commit()


class Notifier:
    def __init__(self, discord_webhook: str, telegram_bot_token: str, telegram_chat_id: str) -> None:
        self.discord_webhook = discord_webhook.strip()
        self.telegram_bot_token = telegram_bot_token.strip()
        self.telegram_chat_id = telegram_chat_id.strip()

    async def send(self, client: httpx.AsyncClient, message: str) -> None:
        tasks = []
        if self.discord_webhook:
            tasks.append(self._send_discord(client, message))
        if self.telegram_bot_token and self.telegram_chat_id:
            tasks.append(self._send_telegram(client, message))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    print(f"[NOTIFIER ERROR] {result}")
        else:
            print("\n--- ALERT ---\n" + message + "\n-------------\n")

    async def _send_discord(self, client: httpx.AsyncClient, message: str) -> None:
        response = await client.post(
            self.discord_webhook,
            json={"content": message[:1900]},
            timeout=20,
        )
        print(f"[DISCORD] status={response.status_code} body={response.text[:200]}")
        response.raise_for_status()

    async def _send_telegram(self, client: httpx.AsyncClient, message: str) -> None:
        url = f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage"
        response = await client.post(
            url,
            json={
                "chat_id": self.telegram_chat_id,
                "text": message[:3900],
                "disable_web_page_preview": False,
            },
            timeout=20,
        )
        response.raise_for_status()


def normalize_text(text: str) -> str:
    text = text or ""
    text = text.lower()
    text = text.replace("’", "'")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_price_eur(text: str) -> Optional[float]:
    text = text or ""
    patterns = [
        r"€\s*([0-9]+(?:[\.,][0-9]{1,2})?)",
        r"([0-9]+(?:[\.,][0-9]{1,2})?)\s*€",
        r"\b([0-9]+[\.,][0-9]{2})\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, text)
        if not m:
            continue
        value = m.group(1)
        try:
            num = float(value.replace(",", "."))
            if 1 <= num <= 1000:
                return num
        except ValueError:
            pass

    return None


def pick_attr(el, *attrs: str) -> Optional[str]:
    for a in attrs:
        v = el.get(a)
        if v:
            return str(v)
    return None


def make_abs_url(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return "https://www.vinted.fi" + url


def guess_listing_id(url: str) -> Optional[str]:
    m = re.search(r"/items/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"(\d{5,})", url)
    return m.group(1) if m else None


def extract_levis_model(text: str) -> Optional[str]:
    text = normalize_text(text)
    m = re.search(r"\b(501|505|511|512|514|517|525|527|541|550|559|560|569)\b", text)
    return m.group(1) if m else None


def extract_size(text: str) -> Optional[str]:
    text = normalize_text(text)

    m = re.search(r"\bw\s?(\d{2})\s*l\s?(\d{2})\b", text)
    if m:
        return f"W{m.group(1)} L{m.group(2)}"

    m = re.search(r"\b(\d{2})\s?[x×]\s?(\d{2})\b", text)
    if m:
        return f"W{m.group(1)} L{m.group(2)}"

    m = re.search(r"\b(xxs|xs|s|m|l|xl|xxl)\b", text, re.I)
    if m:
        return m.group(1).upper()

    m = re.search(r"\b(3[5-9]|4[0-9])([.,]5)?\b", text)
    if m:
        return (m.group(1) + (m.group(2) or "")).replace(",", ".")

    return None


def extract_brand(text: str) -> Optional[str]:
    brand_match = re.search(
        r"\b(nike|adidas|levi(?:'|’)s|levis|levi strauss(?: & co\.)?|carhartt|patagonia|stussy|new balance|umbro|the north face|north face)\b",
        text,
        re.I,
    )
    return brand_match.group(1) if brand_match else None


def extract_condition(text: str) -> Optional[str]:
    cond_match = re.search(
        r"\b(new with tags|new without tags|very good|good|satisfactory)\b",
        text,
        re.I,
    )
    return cond_match.group(1) if cond_match else None


RESALE_ESTIMATES: Dict[str, float] = {
    "nike sb force 58": 35.0,
    "force 58": 35.0,
    "air force 1": 35.0,
    "af1": 35.0,
    "air max 90": 40.0,
    "air max 95": 45.0,
    "air max 97": 45.0,
    "air max plus": 50.0,
    "tn": 50.0,
    "nike dunk": 55.0,
    "dunk": 55.0,
    "adidas samba": 55.0,
    "samba": 55.0,
    "new balance 550": 55.0,
    "new balance 2002r": 70.0,
    "2002r": 70.0,
    "levis 501": 30.0,
    "501": 30.0,
    "levis 505": 28.0,
    "505": 28.0,
    "levis 550": 35.0,
    "levis 560": 35.0,
    "levis 569": 35.0,
    "550 jeans": 35.0,
    "560 jeans": 35.0,
    "569 jeans": 35.0,
    "nike hoodie": 30.0,
    "carhartt hoodie": 45.0,
    "stussy hoodie": 50.0,
    "stussy": 35.0,
    "patagonia fleece": 65.0,
    "north face fleece": 50.0,
    "the north face fleece": 50.0,
    "football shirt": 30.0,
    "retro football shirt": 35.0,
}

NEW_LISTING_MARKERS = [
    "just now",
    "a minute ago",
    "1 minute ago",
    "2 minutes ago",
    "3 minutes ago",
    "4 minutes ago",
    "5 minutes ago",
    "6 minutes ago",
    "7 minutes ago",
    "8 minutes ago",
    "9 minutes ago",
    "10 minutes ago",
    "minute ago",
    "min ago",
]


def estimate_total_buy_cost(price_eur: Optional[float]) -> Optional[float]:
    if price_eur is None:
        return None
    buyer_fee = max(0.70, price_eur * 0.05 + 0.70)
    shipping = float(CONFIG.get("estimated_shipping_eur", 5.50))
    return round(price_eur + buyer_fee + shipping, 2)


def estimate_resale_value(listing: Listing) -> Optional[float]:
    hay = normalize_text(" ".join([
        listing.title or "",
        listing.raw_text or "",
        listing.brand or "",
    ]))

    base: Optional[float] = None
    for key, value in sorted(RESALE_ESTIMATES.items(), key=lambda kv: len(kv[0]), reverse=True):
        if key in hay:
            base = value
            break

    if base is None:
        return None

    if "made in usa" in hay:
        base += 10
    if "vintage" in hay:
        base += 5
    if "sb" in hay and "nike" in hay:
        base += 5
    if "very good" in hay:
        base += 3
    if "new with tags" in hay:
        base += 8
    elif "new without tags" in hay:
        base += 5
    if any(x in hay for x in ["kids", "baby"]):
        base -= 15

    return round(base, 2)


def estimate_profit(listing: Listing) -> Optional[float]:
    resale = estimate_resale_value(listing)
    total_cost = estimate_total_buy_cost(listing.price_eur)
    if resale is None or total_cost is None:
        return None
    return round(resale - total_cost, 2)


def score_listing(listing: Listing) -> int:
    score = 0
    hay = normalize_text(" ".join([
        listing.title or "",
        listing.raw_text or "",
        listing.brand or "",
        listing.condition or "",
    ]))

    profit = estimate_profit(listing)
    if profit is not None:
        if profit >= 25:
            score += 4
        elif profit >= 18:
            score += 3
        elif profit >= 12:
            score += 2
        elif profit >= 8:
            score += 1

    if "new with tags" in hay:
        score += 3
    elif "new without tags" in hay:
        score += 2
    elif "very good" in hay:
        score += 1

    if "made in usa" in hay:
        score += 2
    if "vintage" in hay:
        score += 1
    if "sb" in hay:
        score += 1

    if any(x in hay for x in ["kids", "baby"]):
        score -= 3

    return score


def is_new_listing(listing: Listing) -> bool:
    hay = normalize_text(" ".join([
        listing.title or "",
        listing.raw_text or "",
    ]))
    return any(marker in hay for marker in NEW_LISTING_MARKERS)


def is_priority_hit(listing: Listing) -> bool:
    profit = estimate_profit(listing)
    if profit is None:
        return False
    return profit >= 20


def parse_cards_from_html(html: str) -> List[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: List[Listing] = []

    candidate_links = soup.select('a[href*="/items/"]')[: int(CONFIG.get("max_cards_per_search", 40))]
    seen_urls = set()

    for link in candidate_links:
        href = pick_attr(link, "href")
        if not href:
            continue

        url = make_abs_url(href)
        if url in seen_urls:
            continue
        seen_urls.add(url)

        text = link.get_text(" ", strip=True)

        parent_text = ""
        if link.parent:
            parent_text = link.parent.get_text(" ", strip=True)

        grandparent_text = ""
        if link.parent and link.parent.parent:
            grandparent_text = link.parent.parent.get_text(" ", strip=True)

        title = pick_attr(link, "title", "aria-label") or text or parent_text or grandparent_text
        title = re.sub(r"\s+", " ", title).strip()
        if not title:
            title = "Untitled listing"

        combined_text = f"{text} {parent_text} {grandparent_text}".strip()
        combined_text = re.sub(r"\s+", " ", combined_text)

        price = extract_price_eur(combined_text)

        image_url = None
        img = link.select_one("img")
        if img:
            image_url = pick_attr(img, "src", "data-src")

        size = extract_size(combined_text)
        brand = extract_brand(combined_text)
        condition = extract_condition(combined_text)

        listing_id = guess_listing_id(url)
        if not listing_id:
            continue

        listings.append(
            Listing(
                listing_id=listing_id,
                title=title,
                url=url,
                price_eur=price,
                size=size,
                brand=brand,
                condition=condition,
                image_url=image_url,
                raw_text=combined_text,
            )
        )

    deduped: Dict[str, Listing] = {}
    for item in listings:
        deduped[item.listing_id] = item
    return list(deduped.values())


def matches_search(listing: Listing, search: Dict[str, Any]) -> bool:
    hay = normalize_text(" ".join([
        listing.title or "",
        listing.raw_text or "",
        listing.brand or "",
        listing.size or "",
        listing.condition or "",
    ]))

    must_include = [normalize_text(x) for x in search.get("must_include", []) if x]
    must_not_include = [normalize_text(x) for x in search.get("must_not_include", []) if x]

    if must_include and not any(x in hay for x in must_include):
        return False

    if any(x in hay for x in must_not_include):
        return False

    if search.get("new_only", True) and not is_new_listing(listing):
        return False

    if any(word in hay for word in BAD_SHOE_WORDS):
        return False

    if any(x in hay for x in ["levis", "levi's", "levi strauss", "levi strauss & co"]):
        model = extract_levis_model(hay)
        if model and model not in GOOD_LEVIS_MODELS:
            return False
        if any(word in hay for word in BAD_LEVIS_WORDS):
            return False

    max_price = search.get("max_price_eur")
    if max_price is not None and listing.price_eur is not None:
        if listing.price_eur > float(max_price):
            return False

    min_profit = search.get("min_profit_eur", 0)
    profit = estimate_profit(listing)
    if min_profit and profit is not None:
        if profit < float(min_profit):
            return False

    min_score = search.get("min_score")
    if min_score is not None and score_listing(listing) < int(min_score):
        return False

    allowed_sizes = [str(x).upper() for x in search.get("allowed_sizes", []) if x]
    if allowed_sizes:
        if not listing.size:
            return False
        normalized_size = str(listing.size).upper()
        if normalized_size not in allowed_sizes:
            return False

    return True


def format_alert(search_name: str, listing: Listing) -> str:
    price = f"€{listing.price_eur:.2f}" if listing.price_eur is not None else "unknown"
    resale = estimate_resale_value(listing)
    total_cost = estimate_total_buy_cost(listing.price_eur)
    profit = estimate_profit(listing)
    score = score_listing(listing)
    prefix = "🚨 PRIORITY" if is_priority_hit(listing) else "🔥"

    parts = [
        f"{prefix} {search_name}",
        listing.title,
        f"Price: {price}",
    ]

    if total_cost is not None:
        parts.append(f"Estimated buy cost: €{total_cost:.2f}")
    if resale is not None:
        parts.append(f"Estimated resale: €{resale:.2f}")
    if profit is not None:
        parts.append(f"Potential profit: €{profit:.2f}")

    parts.append(f"Score: {score}")

    if listing.size:
        parts.append(f"Size: {listing.size}")
    if listing.brand:
        parts.append(f"Brand: {listing.brand}")
    if listing.condition:
        parts.append(f"Condition: {listing.condition}")

    parts.append(listing.url)
    return "\n".join(parts)


async def fetch_html(client: httpx.AsyncClient, url: str, user_agent: str) -> str:
    headers = {
        "User-Agent": user_agent,
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }
    response = await client.get(url, headers=headers, follow_redirects=True, timeout=20)
    response.raise_for_status()
    return response.text


async def process_search(
    client: httpx.AsyncClient,
    store: SeenStore,
    notifier: Notifier,
    search: Dict[str, Any],
) -> None:
    try:
        html = await fetch_html(client, search["url"], CONFIG["user_agent"])
        listings = parse_cards_from_html(html)

        if not listings:
            print(f"[{search['name']}] No listings parsed. You may need to adjust selectors.")
            return

        hits = 0
        for listing in listings:
            if store.has(listing.listing_id):
                continue

            if CONFIG.get("debug_raw_text", False):
                print(f"[RAW] {search['name']} | {listing.raw_text[:250]}")

            matched = matches_search(listing, search)
            profit = estimate_profit(listing)
            score = score_listing(listing)

            if CONFIG.get("debug_matches", True):
                print(
                    f"[DEBUG] {search['name']} | "
                    f"title={listing.title[:60]} | "
                    f"price={listing.price_eur} | "
                    f"size={listing.size} | "
                    f"profit={profit} | "
                    f"score={score} | "
                    f"matched={matched}"
                )

            if matched:
                hits += 1
                msg = format_alert(search["name"], listing)
                await notifier.send(client, msg)

            store.add(listing)

        print(f"[{search['name']}] parsed={len(listings)} new_matches={hits}")
    except Exception as exc:
        print(f"[{search.get('name', 'search')}] ERROR: {exc}", file=sys.stderr)


async def run_once(client: httpx.AsyncClient, store: SeenStore, notifier: Notifier) -> None:
    tasks = [
        process_search(client, store, notifier, search)
        for search in CONFIG["searches"]
    ]
    await asyncio.gather(*tasks, return_exceptions=True)


async def main() -> None:
    if not CONFIG["discord_webhook"] and not (
        CONFIG["telegram_bot_token"] and CONFIG["telegram_chat_id"]
    ):
        print("No Discord webhook or Telegram config found. Alerts will print to console.")

    store = SeenStore(CONFIG["db_path"])
    notifier = Notifier(
        CONFIG.get("discord_webhook", ""),
        CONFIG.get("telegram_bot_token", ""),
        CONFIG.get("telegram_chat_id", ""),
    )

    limits = httpx.Limits(max_connections=20, max_keepalive_connections=10)
    timeout = httpx.Timeout(20.0)

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        if CONFIG["discord_webhook"]:
            try:
                await notifier.send(client, "✅ Discord test from Vinted bot")
                print("Discord test sent.")
            except Exception as exc:
                print(f"[DISCORD TEST ERROR] {exc}")

        while True:
            await run_once(client, store, notifier)

            sleep_for = int(CONFIG["poll_seconds"]) + random.randint(
                int(CONFIG.get("sleep_jitter_min", -5)),
                int(CONFIG.get("sleep_jitter_max", 8)),
            )
            if sleep_for < 15:
                sleep_for = 15

            print(f"Sleeping {sleep_for}s...")
            await asyncio.sleep(sleep_for)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Stopped.")