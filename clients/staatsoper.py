"""Hamburgische Staatsoper (die-hamburgische-staatsoper.de) – one URL per
production/event page that has at least one upcoming performance.

Source: https://www.die-hamburgische-staatsoper.de/de/kalender — server-rendered,
contains every upcoming performance of the season on a single page.

A page is only included if at least one of its performances
- has not started yet (Berlin time), and
- is not sold out, i.e. the ticket button does not say "Ausverkauft" or
  "Aktuell keine Karten". ("VVK: <date>" = presale not started yet is kept.)

Labels: the category shown in the calendar ("oper", "ballett", "konzert");
events without a category (guided tours, workshops) get "sonstiges".
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from pagefeed import FeedError, fetch

BASE = "https://www.die-hamburgische-staatsoper.de"
CALENDAR_URL = BASE + "/de/kalender"
MIN_EVENTS = 100         # currently ~420 performances / ~95 URLs
UNAVAILABLE = {"ausverkauft", "aktuell keine karten"}
TZ = ZoneInfo("Europe/Berlin")


def scrape():
    blocks = fetch(CALENDAR_URL).split('class="block block--event')[1:]
    now = datetime.now(TZ)
    rows = {}
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

        if start < now or status in UNAVAILABLE:
            continue
        c = re.search(r'class="event__category"><span>([^<]+)</span>', block)
        label = c.group(1).strip().lower() if c else "sonstiges"
        url = BASE + m.group(1)
        rows[url] = sorted(set(rows.get(url, [])) | {label})

    if n_events and n_buttons < n_events / 2:
        raise FeedError(f"Only {n_buttons}/{n_events} performances have a ticket button – markup may have changed.")
    if n_events and not any(u.split("/")[5] in ("oper", "ballett", "konzert") for u in rows):
        raise FeedError("No /oper/, /ballett/ or /konzert/ URLs found – URL scheme may have changed.")
    return n_events, rows
