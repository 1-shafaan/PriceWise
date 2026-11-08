"""Best-effort Amazon.in product page reader. Amazon may block automated requests."""
from __future__ import annotations

import re
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "en-IN,en;q=0.9"}


def is_amazon_in(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        return host == "amazon.in" or host.endswith(".amazon.in")
    except ValueError:
        return False


def extract_product(url: str) -> dict:
    """Read public page metadata and common Amazon price elements."""
    if not is_amazon_in(url):
        raise ValueError("Please use an amazon.in product link.")
    response = requests.get(url, headers=HEADERS, timeout=12)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    def meta(*keys: str) -> str | None:
        for key in keys:
            tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                return str(tag["content"]).strip()
        return None

    title_tag = soup.select_one("#productTitle")
    title = (title_tag.get_text(" ", strip=True) if title_tag else None) or meta("og:title")
    image = meta("og:image")
    if not image:
        image_tag = soup.select_one("#landingImage")
        image = image_tag.get("src") if image_tag else None

    price_text = None
    for selector in ("#corePrice_feature_div .a-offscreen", "#price_inside_buybox", "#priceblock_ourprice", "#priceblock_dealprice", ".apexPriceToPay .a-offscreen"):
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            price_text = node.get_text(" ", strip=True)
            break
    if not price_text:
        price_text = meta("product:price:amount")
    match = re.search(r"(?:₹|Rs\.?\s*)\s*([\d,]+(?:\.\d{1,2})?)", price_text or "", re.I)
    if not match and price_text:
        match = re.search(r"([\d,]+(?:\.\d{1,2})?)", price_text)
    price = float(match.group(1).replace(",", "")) if match else None
    if not title:
        raise ValueError("Could not read a product title from that page.")
    return {"name": title, "image_url": image, "price": price, "seller": "Amazon.in", "url": response.url}
