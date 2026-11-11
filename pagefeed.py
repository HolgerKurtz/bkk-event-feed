#!/usr/bin/env python3
"""Build Google Ads DSA page feeds (CSV) of upcoming events, one per client.

Each client is a module in clients/ that knows how to scrape its own website.
It must define:

    MIN_EVENTS = 50          # fewer events than this means parsing or the site broke
    def scrape():            # -> (event_count, {page_url: [label, ...]})   page feed only
                             # or (event_count, [item, ...])                 page + product feed

Optional:
    ACTIVE = False           # skip when running all clients (can still be run by name)
    MAX_DROP = 0.5           # fail if the feed shrinks by more than this fraction
    BRAND, LINK              # product feed: g:brand and the channel link (required with items)
    CONVERT_IMAGES = True    # product feed: route images through wsrv.nl to get JPEGs
    MAX_INCOMPLETE = 0.1     # product feed: items without price/image are left out;
                             # fail if more than this fraction is left out

An item is a dict with id, url, title, description, date (datetime), venue,
labels, available (bool), price ("12.00 EUR"), image_url and optionally
additional_images (list of URLs).

Output: feeds/<client>.csv with columns "Page URL", "Custom label"
(labels ";"-separated; available items only), and for item clients also
feeds/<client>.xml, a Google Base RSS feed with all items for Meta catalogs. If anything looks off for a client, its existing CSV is
left untouched and the script exits non-zero (after processing all clients),
so a failing scheduled run alerts us instead of publishing a broken feed.

Usage: python3 pagefeed.py [client ...]     (no args = all active clients)
"""
import csv
import gzip
import importlib
import io
import json
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLIENTS_DIR = ROOT / "clients"
FEEDS_DIR = ROOT / "feeds"


class FeedError(Exception):
    pass


USER_AGENT = "event-pagefeeds/1.0 (+https://github.com/HolgerKurtz/event-pagefeeds)"
REQUEST_PAUSE = 1.0      # seconds between requests, so we never hit a site in bursts
_last_request = 0.0


def fetch(url):
    """GET a page politely: one at a time, paused, gzip-compressed, honest User-Agent."""
    global _last_request
    wait = _last_request + REQUEST_PAUSE - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            if r.status != 200:
                raise FeedError(f"{url} returned HTTP {r.status}")
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return body.decode("utf-8")
    finally:
        _last_request = time.monotonic()


class DetailsCache:
    """Per-page details (image, description, ...) cached in feeds/<client>-details.json,
    so each detail page is fetched once and then only every MAX_AGE_DAYS."""
    MAX_AGE_DAYS = 30

    def __init__(self, name):
        self.path = FEEDS_DIR / f"{name}-details.json"
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.used = set()

    def _fresh(self, entry):
        return date.fromisoformat(entry["fetched"]) > date.today() - timedelta(days=self.MAX_AGE_DAYS)

    def get(self, url, loader, fields=()):
        """Cached details for url; loader(url) -> dict is called if missing, stale or
        lacking one of fields (so adding a field refreshes the cache once).
        If loading fails, the stale entry (or {}) is returned and nothing is cached."""
        self.used.add(url)
        entry = self.data.get(url)
        if entry and self._fresh(entry) and all(f in entry for f in fields):
            return entry
        try:
            entry = {**loader(url), "fetched": date.today().isoformat()}
        except Exception as e:
            print(f"WARNING {url}: details not loaded ({e})", file=sys.stderr)
            return entry or {}
        self.data[url] = entry
        return entry

    def save(self):
        # Drop pages that are gone, but only once stale, so one bad run can't wipe the cache.
        self.data = {u: e for u, e in self.data.items() if u in self.used or self._fresh(e)}
        self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")


def jpeg_url(src):
    """JPEG version of an image via the wsrv.nl proxy (src must keep its own ?w=,
    otherwise the origin redirects and wsrv returns 404)."""
    return f"https://wsrv.nl/?url={urllib.parse.quote(src, safe='')}&w=1200&output=jpg&q=85"


def check_image(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            ctype = r.headers.get("Content-Type", "")
            if r.status != 200 or ctype != "image/jpeg":
                raise FeedError(f"Image check failed: {url} -> HTTP {r.status} {ctype}")
    except urllib.error.URLError as e:
        raise FeedError(f"Image check failed: {url} -> {e}")


REQUIRED = ("id", "title", "url")
OPTIONAL = ("price", "image_url")   # items lacking these are left out of the product feed


def check_items(client, items):
    """Leave out items without price or image (warn); fail on missing ids/titles/links
    or if more than MAX_INCOMPLETE of the items are left out."""
    for it in items:
        missing = [k for k in REQUIRED if not it.get(k)]
        if missing:
            raise FeedError(f"Item {it.get('url')} lacks {', '.join(missing)}.")
    ok = [it for it in items if all(it.get(k) for k in OPTIONAL)]
    dropped = len(items) - len(ok)
    if dropped:
        print(f"{client.__name__.split('.')[-1]}: {dropped} items without price or image left out of product feed")
    if dropped > len(items) * getattr(client, "MAX_INCOMPLETE", 0.1):
        raise FeedError(f"{dropped}/{len(items)} items lack price or image – markup may have changed.")
    if getattr(client, "CONVERT_IMAGES", False):
        for it in ok:
            it["image_url"] = jpeg_url(it["image_url"])
            it["additional_images"] = [jpeg_url(u) for u in it.get("additional_images", [])]
        if ok:
            check_image(ok[0]["image_url"])
    for it in ok:  # Meta splits this field on , ; space and |, so a URL must contain none of them
        bad = [u for u in it.get("additional_images", []) if any(c in u for c in ",; |")]
        if bad:
            raise FeedError(f"Item {it['url']}: additional image URL contains a separator: {bad[0]}")
    return ok


def check(client, n_events, rows, previous_count):
    if n_events < client.MIN_EVENTS:
        raise FeedError(f"Only {n_events} events found (expected >= {client.MIN_EVENTS}) – page structure may have changed.")
    if not any(rows.values()):
        raise FeedError("No URL has a label – label markup may have changed.")
    max_drop = getattr(client, "MAX_DROP", 0.5)
    if previous_count and len(rows) < previous_count * (1 - max_drop):
        raise FeedError(f"Feed would shrink from {previous_count} to {len(rows)} URLs – refusing to overwrite.")


def to_csv(rows):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["Page URL", "Custom label"])
    for url, labels in rows.items():
        w.writerow([url, ";".join(labels)])
    return buf.getvalue()


G = "http://base.google.com/ns/1.0"
ET.register_namespace("g", G)


def to_xml(client, items):
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = client.BRAND
    ET.SubElement(channel, "link").text = client.LINK
    ET.SubElement(channel, "description").text = f"Upcoming events – {client.BRAND}"
    for it in items:
        item = ET.SubElement(channel, "item")
        for tag, value in [
            ("id", it["id"]),
            ("title", it["title"]),
            ("description", it.get("description") or it["title"]),
            ("link", it["url"]),
            ("image_link", it["image_url"]),
            ("price", it["price"]),
            ("availability", "in stock" if it["available"] else "out of stock"),
            ("condition", "new"),
            ("brand", client.BRAND),
            ("custom_label_0", it["date"].strftime("%d.%m.%Y")),
            ("custom_label_1", ";".join(it.get("labels", []))),
            ("custom_label_2", it.get("venue", "")),
            ("custom_label_3", it["date"].strftime("%H:%M")),
        ] + ([("additional_image_link", ",".join(it["additional_images"]))]  # Meta: one field, comma-separated
             if it.get("additional_images") else []):
            ET.SubElement(item, f"{{{G}}}{tag}").text = value
    ET.indent(rss)
    return ET.tostring(rss, encoding="unicode", xml_declaration=True) + "\n"


def previous_lines(path, header=1):
    return max(0, len(path.read_text(encoding="utf-8").splitlines()) - header) if path.exists() else None


def run(name):
    client = importlib.import_module(f"clients.{name}")
    out = FEEDS_DIR / f"{name}.csv"
    xml_out = FEEDS_DIR / f"{name}.xml"
    n_events, result = client.scrape()

    items = None
    if isinstance(result, dict):
        rows = result
    else:
        items = check_items(client, result)
        rows = {it["url"]: it["labels"] for it in result if it["available"]}
    check(client, n_events, rows, previous_lines(out))
    if items is not None and xml_out.exists():
        prev = xml_out.read_text(encoding="utf-8").count("<item>")
        if len(items) < prev * (1 - getattr(client, "MAX_DROP", 0.5)):
            raise FeedError(f"Product feed would shrink from {prev} to {len(items)} items – refusing to overwrite.")

    xml = to_xml(client, items) if items is not None else None
    out.write_text(to_csv(rows), encoding="utf-8")
    labelled = sum(1 for v in rows.values() if v)
    print(f"{name}: {n_events} events, {len(rows)} URLs ({labelled} labelled) -> {out.relative_to(ROOT)}")
    if xml is not None:
        xml_out.write_text(xml, encoding="utf-8")
        in_stock = sum(1 for it in items if it["available"])
        print(f"{name}: {len(items)} items ({in_stock} in stock, {len(items) - in_stock} out of stock)"
              f" -> {xml_out.relative_to(ROOT)}")


def all_clients():
    """Clients with ACTIVE = True (the default)."""
    names = sorted(p.stem for p in CLIENTS_DIR.glob("*.py") if not p.stem.startswith("_"))
    return [n for n in names if getattr(importlib.import_module(f"clients.{n}"), "ACTIVE", True)]


def main():
    sys.path.insert(0, str(ROOT))
    FEEDS_DIR.mkdir(exist_ok=True)
    names = sys.argv[1:] or all_clients()
    failed = []
    for name in names:
        try:
            run(name)
        except Exception as e:
            print(f"ERROR {name}: {e}", file=sys.stderr)
            failed.append(name)
    if failed:
        sys.exit(f"Failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
