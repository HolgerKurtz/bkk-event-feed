"""Hamburgische Staatsoper (die-hamburgische-staatsoper.de) – one item per
production/event page that has at least one upcoming performance.

Source: https://www.die-hamburgische-staatsoper.de/de/kalender — server-rendered,
contains every upcoming performance of the season on a single page.

A performance is "available" if it
- has not started yet (Berlin time), and
- is not sold out, i.e. the ticket button does not say "Ausverkauft" or
  "Aktuell keine Karten". ("VVK: <date>" = presale not started yet counts as available.)

Page feed (CSV): pages with at least one available performance.
Product feed (XML): all pages with a future performance; pages without an
available one are "out of stock".

Labels: the category shown in the calendar ("oper", "ballett", "konzert");
events without a category (guided tours, workshops) get "sonstiges".

Image and description are not in the calendar. They come from the production
page (og:image, the photos of its "Impression" gallery, meta description), cached
in feeds/staatsoper-details.json so only new pages are fetched. og:image is WebP only, so CONVERT_IMAGES routes it
through wsrv.nl to get a JPEG.
"""
import html
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from pagefeed import DetailsCache, FeedError, fetch

ACTIVE = True            # False = skipped when running all clients
BASE = "https://www.die-hamburgische-staatsoper.de"
CALENDAR_URL = BASE + "/de/kalender"
LINK = CALENDAR_URL
BRAND = "Hamburgische Staatsoper"
CONVERT_IMAGES = True
MIN_EVENTS = 100         # currently ~420 performances / ~95 URLs
MAX_INCOMPLETE = 0.5     # ~40 % of pages (mostly side events) have no image or no price
UNAVAILABLE = {"ausverkauft", "aktuell keine karten"}
TZ = ZoneInfo("Europe/Berlin")


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def prices(block):
    """(enabled, all) prices in EUR from the extra__tags line."""
    t = re.search(r'class="extra__tags">(.*?)</div>', block, re.S)
    if not t:
        return [], []
    tags = re.sub(r'<a [^>]*class="tag".*?</a>', "", t.group(1), flags=re.S)  # abo names
    every = re.findall(r"(\d+(?:,\d\d)?)\s*€", tags)
    disabled = re.findall(r'<span class="tag--disabled">\s*(\d+(?:,\d\d)?)\s*€', tags)
    enabled = list(every)
    for p in disabled:
        enabled.remove(p)
    as_float = lambda ps: [float(p.replace(",", ".")) for p in ps]
    return as_float(enabled), as_float(every)


GALLERY_IMG = re.compile(r'<li class="splide__slide">\s*<div class="block--image">.*?'
                         r'data-src="(/external-images/[^"?]+)', re.S)
MAX_ADDITIONAL_IMAGES = 20   # Meta's limit for additional_image_link


def load_details(url):
    page = fetch(url)
    # The production's "Impression" gallery (not sponsor logos or other images on the page).
    gallery = list(dict.fromkeys(BASE + p + "?w=2000" for p in GALLERY_IMG.findall(page)))
    img = re.search(r'<meta property="og:image" content="([^"]+)"', page)
    if img:
        image = html.unescape(img.group(1))
    else:
        # Many side events have no og:image; then the first gallery photo is the main image.
        # Some (tours, school programmes, talks) have no image at all: cached as "" so they
        # aren't re-fetched daily, and left out of the product feed.
        image = gallery[0] if gallery else ""
    file_name = lambda u: u.split("?")[0].rsplit("/", 1)[-1]
    gallery = [g for g in gallery if file_name(g) != file_name(image)][:MAX_ADDITIONAL_IMAGES]
    desc = re.search(r'<meta name="description" content="([^"]*)"', page)
    return {"image": image,
            "gallery": gallery,
            "description": text(desc.group(1)) if desc else ""}


def scrape():
    blocks = fetch(CALENDAR_URL).split('class="block block--event')[1:]
    now = datetime.now(TZ)
    perfs = {}           # url -> [performance, ...] (future only, in calendar order)
    n_events = n_buttons = 0
    for block in blocks:
        m = re.search(r'data-href="(/de/programm/[^"]+)"', block)
        if not m:
            continue  # e.g. the calendar filter controls share the class prefix
        n_events += 1

        d = re.search(r'class="event__date">\w+ (\d{1,2}\.\d{1,2}\.\d\d)</span>\s*'
                      r'<span class="event__datetime">(\d{1,2}:\d\d)', block)
        if not d:
            raise FeedError(f"Performance of {m.group(1)} without date/time – markup may have changed.")
        start = datetime.strptime(" ".join(d.groups()), "%d.%m.%y %H:%M").replace(tzinfo=TZ)

        controls = block[block.find('class="event__controls"'):]
        b = re.search(r'class="btn btn--standard[^"]*"[^>]*>\s*<span class="btn__label">([^<]*)<', controls)
        if b:
            n_buttons += 1
        status = re.sub(r"\s+", " ", b.group(1)).strip().lower() if b else ""

        if start < now:
            continue
        c = re.search(r'class="event__category"><span>([^<]+)</span>', block)
        title = re.search(r'class="event__title">(.*?)</div>', block, re.S)
        subtitle = re.search(r'class="event__subtitle">(.*?)</div>', block, re.S)
        venue = re.search(r'class="event__location">(.*?)</div>', block, re.S)
        enabled, every = prices(block)
        perfs.setdefault(BASE + m.group(1), []).append({
            "start": start,
            "available": status not in UNAVAILABLE,
            "label": c.group(1).strip().lower() if c else "sonstiges",
            "title": text(title.group(1)) if title else "",
            "subtitle": text(subtitle.group(1)) if subtitle else "",
            "venue": text(venue.group(1)) if venue else "",
            "prices": enabled,
            "all_prices": every,
        })

    if n_events and n_buttons < n_events / 2:
        raise FeedError(f"Only {n_buttons}/{n_events} performances have a ticket button – markup may have changed.")
    if n_events and not any(u.split("/")[5] in ("oper", "ballett", "konzert") for u in perfs):
        raise FeedError("No /oper/, /ballett/ or /konzert/ URLs found – URL scheme may have changed.")

    cache = DetailsCache("staatsoper")
    items = []
    for url, ps in perfs.items():
        avail = [p for p in ps if p["available"]]
        relevant = avail or ps
        nxt = min(relevant, key=lambda p: p["start"])
        price = (min((x for p in avail for x in p["prices"]), default=None)
                 or min((x for p in ps for x in p["all_prices"]), default=None))
        details = cache.get(url, load_details, fields=("image", "gallery", "description"))
        items.append({
            "id": url.rstrip("/").rsplit("/", 1)[1],
            "url": url,
            "title": nxt["title"],
            "description": details.get("description") or nxt["subtitle"] or nxt["title"],
            "date": nxt["start"],
            "venue": nxt["venue"],
            "labels": sorted({p["label"] for p in relevant}),
            "available": bool(avail),
            "price": f"{price:.2f} EUR" if price else None,  # none listed: left out of product feed
            "image_url": details.get("image"),
            "additional_images": details.get("gallery", []),
        })
    cache.save()
    return n_events, items
