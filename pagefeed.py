#!/usr/bin/env python3
"""Build Google Ads DSA page feeds (CSV) of upcoming events, one per client.

Each client is a module in clients/ that knows how to scrape its own website.
It must define:

    MIN_EVENTS = 50          # fewer events than this means parsing or the site broke
    def scrape():            # -> (event_count, {page_url: [label, ...]})

Optional: MAX_DROP (default 0.5) – fail if the feed shrinks by more than this
fraction compared with the last run.

Output: feeds/<client>.csv with columns "Page URL", "Custom label"
(labels ";"-separated). If anything looks off for a client, its existing CSV is
left untouched and the script exits non-zero (after processing all clients),
so a failing scheduled run alerts us instead of publishing a broken feed.

Usage: python3 pagefeed.py [client ...]     (no args = all clients)
"""
import csv
import importlib
import io
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLIENTS_DIR = ROOT / "clients"
FEEDS_DIR = ROOT / "feeds"


class FeedError(Exception):
    pass


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (pagefeed)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status != 200:
            raise FeedError(f"{url} returned HTTP {r.status}")
        return r.read().decode("utf-8")


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


def run(name):
    client = importlib.import_module(f"clients.{name}")
    out = FEEDS_DIR / f"{name}.csv"
    previous_count = None
    if out.exists():
        previous_count = max(0, len(out.read_text(encoding="utf-8").splitlines()) - 1)
    n_events, rows = client.scrape()
    check(client, n_events, rows, previous_count)
    out.write_text(to_csv(rows), encoding="utf-8")
    labelled = sum(1 for v in rows.values() if v)
    print(f"{name}: {n_events} events, {len(rows)} URLs ({labelled} labelled) -> {out.relative_to(ROOT)}")


def all_clients():
    return sorted(p.stem for p in CLIENTS_DIR.glob("*.py") if not p.stem.startswith("_"))


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
