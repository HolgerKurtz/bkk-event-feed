#!/usr/bin/env python3
"""Build a Google Ads DSA page feed (CSV) of all upcoming events at bergson.com.

Source: https://bergson.com/events/filter — the internal endpoint the /programm
page calls via AJAX. Without filter params it returns an HTML fragment with
*all* upcoming events (no pagination).

Output columns: "Page URL", "Custom label" (genre slugs, ";"-separated).

Exits non-zero — and leaves the existing CSV untouched — if anything looks off,
so a failing scheduled run alerts us instead of publishing a broken feed.

Usage: python3 pagefeed.py [output.csv]
"""
import csv
import io
import re
import sys
import urllib.request
from pathlib import Path

BASE = "https://bergson.com"
FILTER_URL = BASE + "/events/filter"
MIN_EVENTS = 50          # currently ~280; far fewer means parsing or the site broke
MAX_DROP = 0.5           # fail if the feed shrinks by more than 50% vs. last run


class FeedError(Exception):
    pass


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (bergson-pagefeed)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status != 200:
            raise FeedError(f"{url} returned HTTP {r.status}")
        return r.read().decode("utf-8")


def parse(page):
    items = page.split('class="program-item ')[1:]
    rows = {}
    for item in items:
        m = re.search(r'data-util-url-param="(/event/[^"]+)"', item)
        if not m:
            raise FeedError(f"Event item without /event/ URL: {item[:200]!r}")
        labels = re.findall(r'data-filter-events-mood-slug-param="([^"]+)"', item)
        url = BASE + m.group(1)
        rows[url] = sorted(set(rows.get(url, [])) | set(labels))
    return items, rows


def check(items, rows, previous_count):
    if len(items) < MIN_EVENTS:
        raise FeedError(f"Only {len(items)} events found (expected >= {MIN_EVENTS}) – page structure may have changed.")
    if not any(rows.values()):
        raise FeedError("No event has a genre label – label markup may have changed.")
    if previous_count and len(rows) < previous_count * (1 - MAX_DROP):
        raise FeedError(f"Feed would shrink from {previous_count} to {len(rows)} URLs – refusing to overwrite.")


def to_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Page URL", "Custom label"])
    for url, labels in rows.items():
        w.writerow([url, ";".join(labels)])
    return buf.getvalue()


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "pagefeed.csv")
    previous_count = None
    if out.exists():
        previous_count = max(0, len(out.read_text(encoding="utf-8").splitlines()) - 1)
    try:
        items, rows = parse(fetch(FILTER_URL))
        check(items, rows, previous_count)
    except Exception as e:
        sys.exit(f"ERROR: {e}")
    out.write_text(to_csv(rows), encoding="utf-8")
    labelled = sum(1 for v in rows.values() if v)
    print(f"{len(rows)} URLs ({labelled} with genre labels) -> {out}")


if __name__ == "__main__":
    main()
