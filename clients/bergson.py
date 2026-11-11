"""Bergson (bergson.com) – all upcoming events, labelled with genre slugs.

Source: https://bergson.com/events/filter — the internal endpoint the /programm
page calls via AJAX. Without filter params it returns an HTML fragment with
*all* upcoming events (no pagination).

Labels: genre slugs like "klassik;jazz".
"""
import re

from pagefeed import FeedError, fetch

BASE = "https://bergson.com"
FILTER_URL = BASE + "/events/filter"
MIN_EVENTS = 50          # currently ~280


def scrape():
    items = fetch(FILTER_URL).split('class="program-item ')[1:]
    rows = {}
    for item in items:
        m = re.search(r'data-util-url-param="(/event/[^"]+)"', item)
        if not m:
            raise FeedError(f"Event item without /event/ URL: {item[:200]!r}")
        labels = re.findall(r'data-filter-events-mood-slug-param="([^"]+)"', item)
        url = BASE + m.group(1)
        rows[url] = sorted(set(rows.get(url, [])) | set(labels))
    return len(items), rows
