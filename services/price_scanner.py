import requests
from bs4 import BeautifulSoup
import re
import random
import time
import json
import os
import boto3
from services import db

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
]


def _parse_price(text):
    """Extract price from text, return as string or empty."""
    m = re.search(r'\$?([\d,]+\.?\d*)', text)
    return m.group(1).replace(",", "") if m else ""


def _scrape_costcoinsider_weekly() -> list:
    """Scrape Costco Insider weekly deals page and parse deal images with Nova 2 Lite."""
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        r = requests.get("https://www.costcoinsider.com/", headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Find the latest weekly insider deals post
        deals_url = None
        for a in soup.select("a[href*='costco']"):
            href = a.get("href", "")
            if re.search(r'costco-.*weekly-insider-deals', href):
                deals_url = href if href.startswith("http") else "https://www.costcoinsider.com" + href
                break

        if not deals_url:
            print("  No weekly deals link found on costcoinsider.com")
            return deals

        print(f"  Weekly deals URL: {deals_url}")
        r = requests.get(deals_url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        content = soup.select_one(".entry-content") or soup.select_one("#content article") or soup.select_one("#content")
        if not content:
            return deals

        # Parse text-format deals from lists (e.g. "Product Name $12.99 – $3.50 off = $9.49")
        for li in content.select("li"):
            text = li.get_text(strip=True)
            prices = re.findall(r'\$([\d,]+\.?\d*)', text)
            if len(prices) >= 1:
                name_part = text.split("$")[0].strip().rstrip(" -–|:")
                name_part = re.sub(r'^\d+\.\s*', '', name_part).strip()
                if 5 < len(name_part) < 100:
                    # Try to identify sale price vs original price
                    sale = prices[-1].replace(",", "")  # last price is typically final
                    orig = prices[0].replace(",", "") if len(prices) > 1 else ""
                    # If "off" appears, the middle price is savings, last is final
                    if "off" in text.lower() and len(prices) >= 3:
                        orig = prices[0].replace(",", "")
                        sale = prices[-1].replace(",", "")
                    deals.append({
                        "item_name": name_part[:100],
                        "sale_price": sale,
                        "original_price": orig,
                        "promo_start": "",
                        "promo_end": "",
                        "source": "costcoinsider.com/weekly",
                        "link": deals_url,
                    })

        # Parse deal images from gallery/content
        images = content.select("img[src*='wp-content/uploads']")
        # Also check for gallery images
        for gallery in content.select(".gallery, .wp-block-gallery, .tiled-gallery"):
            images.extend(gallery.select("img[src]"))
        # Deduplicate by src
        seen_srcs = set()
        unique_images = []
        for img in images:
            src = img.get("src", "") or img.get("data-src", "")
            if src and src not in seen_srcs and "logo" not in src.lower() and "icon" not in src.lower():
                seen_srcs.add(src)
                unique_images.append(src)

        for img_url in unique_images[:20]:
            try:
                img_r = requests.get(img_url, headers=headers, timeout=15)
                if img_r.status_code != 200:
                    continue
                content_type = img_r.headers.get("content-type", "")
                if "image" not in content_type:
                    continue
                fmt = "jpeg"
                if "png" in content_type:
                    fmt = "png"
                elif "webp" in content_type:
                    fmt = "webp"

                resp = _bedrock.converse(
                    modelId="us.amazon.nova-2-lite-v1:0",
                    messages=[{"role": "user", "content": [
                        {"image": {"format": fmt, "source": {"bytes": img_r.content}}},
                        {"text": COUPON_PROMPT},
                    ]}],
                    inferenceConfig={"maxTokens": 4096, "temperature": 0},
                )
                text = resp["output"]["message"]["content"][0]["text"]
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]

                items = json.loads(text.strip())
                for item in items:
                    sale = item.get("sale_price", "")
                    savings = item.get("savings", "")
                    name = item.get("name", "").strip()
                    item_num = item.get("item_number", "").strip()
                    if name and (sale or savings):
                        deals.append({
                            "item_name": name[:100],
                            "item_number": item_num,
                            "sale_price": sale.replace(",", "") if sale else "",
                            "original_price": "",
                            "promo_start": "",
                            "promo_end": "",
                            "source": "costcoinsider.com/weekly",
                            "link": deals_url,
                        })
                print(f"    Image parsed: {len(items)} items")
            except Exception as e:
                print(f"    Image parse failed: {e}")

    except Exception as e:
        print(f"Costco Insider weekly scrape failed: {e}")
    return deals


def _scrape_costcoinsider_coupon_book() -> list:
    """Scrape Costco Insider coupon book page and parse coupon images with Nova 2 Lite."""
    deals = []
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        r = requests.get("https://www.costcoinsider.com/", headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Find the latest coupon book post (skip generic "upcoming" page)
        coupon_url = None
        for a in soup.select("a[href*='costco']"):
            href = a.get("href", "")
            if re.search(r'costco-.*coupon-book', href) and "upcoming" not in href:
                coupon_url = href if href.startswith("http") else "https://www.costcoinsider.com" + href
                break

        if not coupon_url:
            print("  No coupon book link found on costcoinsider.com")
            return deals

        print(f"  Coupon book URL: {coupon_url}")
        r = requests.get(coupon_url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        content = soup.select_one(".entry-content") or soup.select_one("#content article") or soup.select_one("#content")
        if not content:
            return deals

        # Find coupon page images
        images = content.select("img[src*='wp-content/uploads']")
        seen_srcs = set()
        unique_images = []
        for img in images:
            src = img.get("src", "") or img.get("data-src", "")
            if src and src not in seen_srcs and "logo" not in src.lower() and "icon" not in src.lower():
                seen_srcs.add(src)
                unique_images.append(src)

        for idx, img_url in enumerate(unique_images[:20]):
            try:
                img_r = requests.get(img_url, headers=headers, timeout=15)
                if img_r.status_code != 200:
                    continue
                content_type = img_r.headers.get("content-type", "")
                if "image" not in content_type:
                    continue
                fmt = "jpeg"
                if "png" in content_type:
                    fmt = "png"
                elif "webp" in content_type:
                    fmt = "webp"

                resp = _bedrock.converse(
                    modelId="us.amazon.nova-2-lite-v1:0",
                    messages=[{"role": "user", "content": [
                        {"image": {"format": fmt, "source": {"bytes": img_r.content}}},
                        {"text": COUPON_PROMPT},
                    ]}],
                    inferenceConfig={"maxTokens": 4096, "temperature": 0},
                )
                text = resp["output"]["message"]["content"][0]["text"]
                if "```" in text:
                    text = text.split("```")[1]
                    if text.startswith("json"):
                        text = text[4:]

                items = json.loads(text.strip())
                for item in items:
                    sale = item.get("sale_price", "")
                    savings = item.get("savings", "")
                    name = item.get("name", "").strip()
                    item_num = item.get("item_number", "").strip()
                    if name and (sale or savings):
                        deals.append({
                            "item_name": name[:100],
                            "item_number": item_num,
                            "sale_price": sale.replace(",", "") if sale else "",
                            "original_price": "",
                            "promo_start": "",
                            "promo_end": "",
                            "source": "costcoinsider.com/coupon-book",
                            "link": coupon_url,
                        })
                print(f"    Page {idx + 1}: {len(items)} items")
            except Exception as e:
                print(f"    Page {idx + 1} parse failed: {e}")

    except Exception as e:
        print(f"Costco Insider coupon book scrape failed: {e}")
    return deals


def _scrape_reddit(subreddit: str) -> list:
    """Scrape a Reddit subreddit for Costco deals with $ in title."""
    deals = []
    try:
        resp = requests.get(
            f"https://www.reddit.com/r/{subreddit}/search.json?q=%24&restrict_sr=on&sort=new&t=month&limit=25",
            headers={"User-Agent": "CostcoScanner/1.0"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        for post in data.get("data", {}).get("children", []):
            post_data = post["data"]
            title = post_data["title"]
            permalink = post_data.get("permalink", "")

            # Skip meta posts
            if any(skip in title.lower() for skip in ["megathread", "thread", "how costco gets you"]):
                continue

            if "$" in title:
                prices = re.findall(r'\$([\d,]+\.?\d*)', title)
                if prices:
                    name_part = title.split("$")[0].strip().rstrip(" -–|:")
                    name_part = re.sub(r'^(Found|Spotted|Deal|Sale|Price|Clearance):\s*', '', name_part, flags=re.IGNORECASE).strip()

                    if 5 < len(name_part) < 80:
                        deals.append({
                            "item_name": name_part,
                            "sale_price": prices[0].replace(",", ""),
                            "original_price": prices[1].replace(",", "") if len(prices) > 1 else "",
                            "promo_start": "",
                            "promo_end": "",
                            "source": f"reddit.com/r/{subreddit}",
                            "link": f"https://www.reddit.com{permalink}" if permalink else "",
                        })
    except Exception as e:
        print(f"Reddit r/{subreddit} failed: {e}")
    return deals


COUPON_PROMPT = """This is a Costco coupon book page. Extract every product deal.
Costco coupon books show: product name, item number (5-7 digit number), a SAVINGS amount (e.g. "$4 OFF" or "SAVE $5"), and sometimes the final price AFTER discount.

Return ONLY a valid JSON array:
[{"name": "PRODUCT NAME", "item_number": "1234567", "sale_price": "12.99", "savings": "4.00"}]

CRITICAL RULES:
- item_number = the Costco item/product number (5-7 digits, usually near the product name). Empty string if not visible.
- sale_price = the FINAL price the customer pays (the lower number). If only a savings amount is shown with no final price, leave sale_price empty.
- savings = the dollar amount saved (the OFF/SAVE amount)
- Do NOT put the savings amount in sale_price
- Skip headers, dates, fine print, non-product items"""

_bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def scan_price_drops(force_refresh: bool = False) -> list:
    """Scan for Costco price drops from verified working sources."""

    if not force_refresh:
        cached_count = db.get_cached_deals_count()
        if cached_count > 0:
            print(f"Using {cached_count} cached deals from today")
            return db.get_all_price_drops()

    print("Fresh scan from working sources...")

    all_deals = []
    sources = [
        ("Costco Insider Weekly", _scrape_costcoinsider_weekly),
        ("Costco Insider Coupon Book", _scrape_costcoinsider_coupon_book),
        ("Reddit r/Costco", lambda: _scrape_reddit("Costco")),
    ]

    for name, scraper in sources:
        try:
            deals = scraper()
            all_deals.extend(deals)
            print(f"  {name}: {len(deals)} deals")
        except Exception as e:
            print(f"  {name}: FAILED - {e}")
        time.sleep(1)  # Rate limit

    # Deduplicate by normalized name
    seen = set()
    saved = []
    for deal in all_deals:
        key = (deal["item_name"].lower().strip(), deal.get("promo_end", ""))
        if key not in seen and not db.item_exists(deal["item_name"], deal["source"], deal.get("promo_end", "")):
            seen.add(key)
            saved.append(db.put_price_drop(**deal))

    print(f"Saved {len(saved)} deals (skipped {len(all_deals) - len(saved)} duplicates)")
    return saved
