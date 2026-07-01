#!/usr/bin/env python3
"""
Google Maps business scraper.

Searches Google Maps for a query (e.g. "lawyer in new york"), walks the list of
businesses, opens each one, and appends name / rating / reviews / phone /
website / address to a CSV file.

Deterministic script only -- no AI. Uses a real Chromium browser via Playwright
because Google renders its data with JavaScript and blocks plain HTTP clients.

Usage:
    python scrape.py "lawyer in new york"
    python scrape.py "dentist in chicago" --out dentist_data.csv --max 60
    python scrape.py "plumber in austin" --headless
"""

import argparse
import csv
import json
import os
import random
import sys
import time
import urllib.parse

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout


# Fields written to the CSV, in order.
CSV_FIELDS = ["name", "rating", "reviews", "phone", "website", "address", "query"]

# Where the browser stores cookies/consent so we don't get re-prompted each run.
PROFILE_DIR = ".gmaps_profile"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def human_pause(lo=0.6, hi=1.6):
    """Sleep a random short amount to look less robotic."""
    time.sleep(random.uniform(lo, hi))


def force_english(url):
    """Append hl=en so Google serves English (numbers/labels), not the OS locale."""
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}hl=en"


def load_existing_keys(path):
    """Return a set of (name, address) already present in the CSV (for dedupe)."""
    keys = set()
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return keys
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                keys.add((row.get("name", ""), row.get("address", "")))
    except Exception as exc:  # noqa: BLE001 - corrupt/old file shouldn't kill the run
        print(f"  ! could not read existing CSV ({exc}); starting fresh", file=sys.stderr)
    return keys


def progress_path_for(out_path):
    """Sidecar file that records which place URLs have been visited, per query."""
    return out_path + ".progress.json"


def load_progress(out_path):
    """Return the progress dict {query: [visited_url, ...]} (empty if none)."""
    path = progress_path_for(out_path)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - corrupt file shouldn't kill the run
        return {}


def save_progress(out_path, progress):
    """Persist the progress dict (called after each listing so it's crash-safe)."""
    try:
        with open(progress_path_for(out_path), "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=0)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not save progress ({exc})", file=sys.stderr)


def open_csv_writer(path):
    """Open the CSV in append mode, writing the header if the file is new/empty."""
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0
    f = open(path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    if is_new:
        writer.writeheader()
        f.flush()
    return f, writer


def dismiss_consent(page):
    """Click the Google cookie/consent 'Accept all' button if it appears."""
    for getter in (
        lambda: page.get_by_role("button", name="Accept all"),
        lambda: page.get_by_role("button", name="Reject all"),
        lambda: page.locator('button[aria-label="Accept all"]'),
    ):
        try:
            btn = getter()
            if btn.count() > 0:
                btn.first.click(timeout=3000)
                human_pause()
                return
        except PWTimeout:
            pass
        except Exception:  # noqa: BLE001
            pass


def collect_place_urls(page, max_results):
    """Scroll the results feed and collect unique place URLs (order-preserving)."""
    try:
        page.wait_for_selector('a.hfpxzc', timeout=20000)
    except PWTimeout:
        print("  ! no results feed appeared -- possibly blocked or zero results", file=sys.stderr)
        return []

    feed = page.locator('div[role="feed"]')
    seen = []
    seen_set = set()
    stale_rounds = 0

    while len(seen) < max_results and stale_rounds < 5:
        links = page.locator('a.hfpxzc')
        count = links.count()
        for i in range(count):
            href = links.nth(i).get_attribute("href")
            if href and href not in seen_set:
                seen_set.add(href)
                seen.append(href)

        if len(seen) >= max_results:
            break

        # Reached the end-of-list marker?
        if page.get_by_text("You've reached the end of the list").count() > 0:
            break

        before = count
        try:
            feed.evaluate("el => el.scrollTo(0, el.scrollHeight)")
        except Exception:  # noqa: BLE001 - fall back to keyboard scroll
            page.mouse.wheel(0, 3000)
        human_pause(1.0, 2.0)

        # Did new cards load?
        stale_rounds = stale_rounds + 1 if page.locator('a.hfpxzc').count() == before else 0

    return seen[:max_results]


def _text_or_blank(page, selector, timeout=2500):
    try:
        loc = page.locator(selector).first
        loc.wait_for(timeout=timeout)
        return (loc.inner_text() or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _attr_or_blank(page, selector, attr, timeout=2500):
    try:
        loc = page.locator(selector).first
        loc.wait_for(timeout=timeout)
        return (loc.get_attribute(attr) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def extract_business(page, url, query):
    """Open a place URL and pull the fields we care about. Never raises."""
    data = {field: "" for field in CSV_FIELDS}
    data["query"] = query

    try:
        page.goto(force_english(url), wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("h1", timeout=15000)
    except Exception as exc:  # noqa: BLE001
        print(f"  ! failed to open place ({exc})", file=sys.stderr)
        return data

    human_pause(0.5, 1.2)

    # Name
    data["name"] = _text_or_blank(page, "h1")

    # Rating + reviews live together in div.F7nice, e.g. "4.5\n(123)"
    rating_block = _text_or_blank(page, "div.F7nice")
    if rating_block:
        parts = rating_block.replace("\n", " ").split()
        if parts:
            data["rating"] = parts[0]
        # reviews count is the bit in parentheses, strip non-digits
        if "(" in rating_block:
            reviews = rating_block[rating_block.find("(") + 1 : rating_block.find(")")]
            data["reviews"] = "".join(ch for ch in reviews if ch.isdigit())

    # Phone: data-item-id looks like "phone:tel:+1 212-555-1234"
    phone_id = _attr_or_blank(page, 'button[data-item-id^="phone:tel:"]', "data-item-id")
    if phone_id:
        data["phone"] = phone_id.split("phone:tel:", 1)[-1].strip()

    # Website: the real URL is the href of the authority link
    data["website"] = _attr_or_blank(page, 'a[data-item-id="authority"]', "href")

    # Address: aria-label is like "Address: 123 Main St..."
    addr = _attr_or_blank(page, 'button[data-item-id="address"]', "aria-label")
    if addr:
        data["address"] = addr.split("Address:", 1)[-1].strip()

    return data


def run(query, out_path, max_results, headless, resume):
    search_url = force_english(
        "https://www.google.com/maps/search/" + urllib.parse.quote_plus(query)
    )

    existing = load_existing_keys(out_path)
    csv_file, writer = open_csv_writer(out_path)
    saved = 0

    # Progress: URLs already visited for this query. We always *record* progress;
    # we only *skip* previously-visited listings when --resume is passed.
    progress = load_progress(out_path)
    done_before = set(progress.get(query, []))
    visited = set(done_before)  # accumulates this run and gets persisted
    if resume and done_before:
        print(f"-> resume: {len(done_before)} listing(s) already visited for this query will be skipped")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=headless,
            slow_mo=120,
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        page = context.pages[0] if context.pages else context.new_page()

        print(f"-> searching Google Maps for: {query}")
        page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
        dismiss_consent(page)
        human_pause(1.0, 2.0)

        print("-> collecting business links (scrolling feed)...")
        urls = collect_place_urls(page, max_results)
        print(f"-> found {len(urls)} listings; visiting each")

        for idx, url in enumerate(urls, 1):
            # Resume: don't even re-open a listing we already processed.
            if resume and url in done_before:
                print(f"  [{idx}/{len(urls)}] resume: already visited, skipping")
                continue

            data = extract_business(page, url, query)
            key = (data["name"], data["address"])

            if not data["name"]:
                # Likely a transient failure -- leave it unmarked so it retries later.
                print(f"  [{idx}/{len(urls)}] skipped (no name extracted)")
            elif key in existing:
                print(f"  [{idx}/{len(urls)}] dup, skipping: {data['name']}")
                visited.add(url)
            else:
                writer.writerow(data)
                csv_file.flush()
                existing.add(key)
                visited.add(url)
                saved += 1
                print(
                    f"  [{idx}/{len(urls)}] saved: {data['name']} | "
                    f"{data['rating'] or '-'}★ | {data['phone'] or 'no phone'} | "
                    f"{data['website'] or 'no site'}"
                )

            # Persist progress after each listing so an interrupted run can resume.
            progress[query] = sorted(visited)
            save_progress(out_path, progress)

            human_pause(0.8, 1.8)

        context.close()

    csv_file.close()
    print(f"\nDone. {saved} new business(es) appended to {out_path}")


def main():
    # Make sure non-ASCII business names/addresses don't crash the Windows console.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - older/odd streams
            pass

    parser = argparse.ArgumentParser(
        description="Scrape business data from Google Maps into a CSV."
    )
    parser.add_argument("query", help='Search text, e.g. "lawyer in new york"')
    parser.add_argument("--out", default="lawyer_data.csv", help="Output CSV file")
    parser.add_argument("--max", type=int, default=40, help="Max businesses to collect")
    parser.add_argument("--headless", action="store_true", help="Run without a visible window")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip listings already visited for this query in a previous run",
    )
    args = parser.parse_args()

    try:
        run(args.query, args.out, args.max, args.headless, args.resume)
    except KeyboardInterrupt:
        print("\nInterrupted -- partial results were already saved.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
