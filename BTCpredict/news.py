# news.py
import streamlit as st
import requests
import xml.etree.ElementTree as ET


@st.cache_data(ttl=300)
def get_crypto_news(limit: int = 3):
    """
    Lightweight RSS news fetch (no API key).
    Uses CoinDesk RSS. Returns: [{"title":..., "url":...}, ...]
    """
    url = "https://feeds.feedburner.com/CoinDesk"
    headers = {"User-Agent": "Mozilla/5.0"}

    r = requests.get(url, timeout=8, headers=headers)
    r.raise_for_status()

    root = ET.fromstring(r.text)

    # RSS: channel/item/title + link
    items = []
    for item in root.findall("./channel/item"):
        title_el = item.find("title")
        link_el = item.find("link")
        if title_el is None or link_el is None:
            continue

        title = (title_el.text or "").strip()
        link = (link_el.text or "").strip()
        if title and link:
            items.append({"title": title, "url": link})
        if len(items) >= limit:
            break

    return items
