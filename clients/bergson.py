"""Bergson (bergson.com) – one item per upcoming event, labelled with genre slugs.

Source: https://bergson.com/events/filter — the internal endpoint the /programm
page calls via AJAX. Without filter params it returns an HTML fragment with
*all* upcoming events (no pagination). Every event has its own /event/<slug> page.

Page feed (CSV): events that aren't sold out ("Ausverkauft" in program-extra).
Product feed (XML): all events; sold-out ones are "out of stock".

Labels: genre slugs like "klassik;jazz".

Image: the list thumbnail /file/format/<n>/list_thumbnail/<h>/<f> rewritten to
the original /file/show/<n>/<h>/<f> (JPEG/PNG/GIF, no conversion needed).
Description: the meta description of the event page; falls back to the subtitle.
Price: bergson.com shows none, so it comes from the event's ticket shop page
(tickets.bergson.com/de/buyingflow/tickets/<group>/<event>/, linked as "Tickets"):
the lowest value of the price range in the row of that event, e.g. "€ 15,00 - 35,00".
A range starting at € 0,00 ("€ 0,00 - 56,00") only means there is a free companion
ticket (Rollstuhlbegleitung); the real lowest prices aren't on the page (they load via
JavaScript), so those events get SOCIAL_PRICE (Bergson's usual social price).
Calendar-style shop pages (gallery tours) have no table; there the JSON list
`var events` gives MinPrice/MaxPrice/SoldOut instead.
Links to a series (/tickets/<group>/ without an event number) list several dates;
the row is then found by date and time.
"Ausverkauft!" in the shop row also makes the event out of stock; it then keeps the
last known price (the shop no longer shows one).
Events without a ticket link or price are left out of the product feed.
Description and price are cached in feeds/bergson-details.json (30 days, see
DetailsCache) and re-fetched when the ticket link changes.
"""
import html
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from pagefeed import DetailsCache, FeedError, fetch

ACTIVE = True            # False = skipped when running all clients
BASE = "https://bergson.com"
FILTER_URL = BASE + "/events/filter"
LINK = BASE + "/programm"
BRAND = "Bergson"
MIN_EVENTS = 50          # currently ~240
MAX_INCOMPLETE = 0.2     # ~7 % have no ticket link (brunches, guided tours, free events)
SOCIAL_PRICE = 28.0      # for ranges starting at 0 (capped at the range's upper end)
TZ = ZoneInfo("Europe/Berlin")
MONTHS = {"jan": 1, "feb": 2, "märz": 3, "mär": 3, "apr": 4, "mai": 5, "juni": 6, "jun": 6,
          "juli": 7, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "okt": 10, "nov": 11, "dez": 12}


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def field(item, cls):
    m = re.search(rf'<div class="{cls}">(.*?)</div>', item, re.S)
    return text(m.group(1)) if m else ""


def start_time(item, url):
    """'Fr. 13. Nov. 2026' + '19.00 Uhr - 20.30 Uhr' -> datetime (Berlin)."""
    d = re.fullmatch(r"\w+\.? (\d{1,2})\. (\w+)\.? (\d{4})", field(item, "event-date"))
    t = re.match(r"(\d{1,2})[.:](\d\d)", field(item, "time"))
    if not d or not t or d.group(2).lower() not in MONTHS:
        raise FeedError(f"{url}: date/time not understood ({field(item, 'event-date')!r} "
                        f"{field(item, 'time')!r}) – markup may have changed.")
    return datetime(int(d.group(3)), MONTHS[d.group(2).lower()], int(d.group(1)),
                    int(t.group(1)), int(t.group(2)), tzinfo=TZ)


TICKET_URL = re.compile(r'class="program-ticket[^"]*" href="'
                        r'(https://tickets\.bergson\.com/[a-z]{2}/buyingflow/tickets/\d+/(?:(\d+)/?)?)"')


def price_from_range(low, high):
    return min(SOCIAL_PRICE, high) if low == 0 and high > 0 else low


def calendar_info(page, event_no, start):
    """Calendar-style shop pages (e.g. gallery tours) have no table, but a JSON list
    `var events = [...]` with MinPrice, MaxPrice and SoldOut per event."""
    m = re.search(r"var events = (\[)", page)
    if not m:
        return None
    events = json.JSONDecoder().raw_decode(page, m.start(1))[0]
    ev = next((e for e in events if str(e.get("Id")) == str(event_no)), None) or next(
        (e for e in events if e.get("DateOfShowString") == f"{start:%d.%m.%Y}"
         and e.get("TimeOfShowString") == f"{start:%H:%M}"), None)
    if not ev:
        return None
    if ev.get("SoldOut"):
        return None, True
    if ev.get("MinPrice") is None or ev.get("MaxPrice") is None:
        return None, False
    return price_from_range(float(ev["MinPrice"]), float(ev["MaxPrice"])), False


def ticket_info(ticket_url, event_no, start):
    """(price or None, sold out?) from this event's row in the ticket shop."""
    page = fetch(ticket_url)
    cal = calendar_info(page, event_no, start)
    if cal:
        return cal
    rows = page.split('class="event-select')[1:]
    row = next((r for r in rows if f'data-value="{event_no}"' in r[:300]), None) if event_no else None
    if row is None:   # series link: match by date and time
        row = next((r for r in rows if f'{start:%d.%m.%Y}</td>' in r and f'>{start:%H:%M}</td>' in r), None)
    cell = re.search(r'<td class="price[^"]*">(.*?)</td>', row or "", re.S)
    if not cell:
        return None, False
    if "ausverkauft" in cell.group(1).lower():
        return None, True
    found = [float(p.replace(".", "").replace(",", ".")) for p in
             re.findall(r"(\d{1,3}(?:\.\d{3})*,\d\d)", cell.group(1))]
    if not found:
        return None, False
    return price_from_range(min(found), max(found)), False


def load_details(url, ticket, start, prev, listed_sold_out):
    page = fetch(url)
    desc = re.search(r'<meta name="description" content="([^"]*)"', page)
    price, sold_out = ticket_info(ticket.group(1), ticket.group(2), start) if ticket else (None, False)
    if price is None and (sold_out or listed_sold_out):
        price = prev.get("price")   # keep the last known price for the out-of-stock item
    return {"description": text(desc.group(1)) if desc else "",
            "ticket": ticket.group(1) if ticket else "",
            "price": price,
            "sold_out": sold_out}


def scrape():
    events = fetch(FILTER_URL).split('class="program-item ')[1:]
    cache = DetailsCache("bergson")
    items = []
    for ev in events:
        m = re.search(r'data-util-url-param="(/event/([^"]+))"', ev)
        if not m:
            raise FeedError(f"Event item without /event/ URL: {ev[:200]!r}")
        url = BASE + m.group(1)
        title = field(ev, "program-title")
        subtitle = field(ev, "program-subtitle")
        img = re.search(r'data-lazy-load-img-src="/file/format/(\d+)/list_thumbnail/([^"]+)"', ev)
        start = start_time(ev, url)
        ticket = TICKET_URL.search(ev)
        prev = cache.data.get(url, {})
        if prev.get("ticket", "") != (ticket.group(1) if ticket else ""):
            cache.data.pop(url, None)   # ticket link changed: price must be re-read
        listed_sold_out = "ausverkauft" in field(ev, "program-extra").lower()
        details = cache.get(url, lambda u: load_details(u, ticket, start, prev, listed_sold_out),
                            fields=("description", "ticket", "price", "sold_out"))
        price = details.get("price")
        items.append({
            "id": m.group(2),
            "url": url,
            "title": title,
            "description": details.get("description") or subtitle or title,
            "date": start,
            "venue": field(ev, "location"),
            "labels": sorted(set(re.findall(r'data-filter-events-mood-slug-param="([^"]+)"', ev))),
            "available": not listed_sold_out and not details.get("sold_out"),
            "price": f"{price:.2f} EUR" if price is not None else None,  # none: left out of product feed
            # Some file names contain commas; encode them so no feed consumer splits the URL.
            "image_url": (f"{BASE}/file/show/{img.group(1)}/{html.unescape(img.group(2))}".replace(",", "%2C")
                          if img else None),
        })
    cache.save()
    return len(events), items
