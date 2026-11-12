# Event feeds for Google Ads (DSA) and Meta catalogs

Daily feeds with all upcoming events, per client:
- a Google Ads **page feed** (`feeds/<client>.csv`), and
- for clients that support it, a Meta **product feed** (`feeds/<client>.xml`, Google Base RSS).

| Client | Source | Labels | Page feed (CSV) | Product feed (XML) |
|---|---|---|---|---|
| `bergson` | bergson.com/events/filter (sold-out events only in the product feed) | genre slugs (`klassik;jazz`) | https://raw.githubusercontent.com/HolgerKurtz/event-pagefeeds/main/feeds/bergson.csv | https://raw.githubusercontent.com/HolgerKurtz/event-pagefeeds/main/feeds/bergson.xml |
| `staatsoper` | die-hamburgische-staatsoper.de/de/kalender (only pages with ≥1 future, not sold-out performance) | `oper` / `ballett` / `konzert` / `sonstiges` | https://raw.githubusercontent.com/HolgerKurtz/event-pagefeeds/main/feeds/staatsoper.csv | https://raw.githubusercontent.com/HolgerKurtz/event-pagefeeds/main/feeds/staatsoper.xml |

- `pagefeed.py` – shared runner: fetches, sanity-checks and writes the feeds. Python stdlib only.
- `clients/<client>.py` – one scraper per website.
- `.github/workflows/update-pagefeed.yml` – runs daily, commits changed feeds.

Run locally: `python3 pagefeed.py` (all active clients) or `python3 pagefeed.py staatsoper`.

## Turning clients on and off

Each client module has `ACTIVE = True`. Set it to `False` and the daily run skips that
client; its last feeds stay online unchanged. `python3 pagefeed.py <name>` still runs an
inactive client by hand.

## Product feed (Meta catalog)

One item per production page. Fields: `g:id` (URL slug), `g:title`, `g:description`,
`g:link`, `g:image_link`, `g:price` (lowest available ticket price, e.g. `7.00 EUR`),
`g:availability` (`in stock` / `out of stock` = every future performance sold out),
`g:condition`, `g:brand`, and
`g:custom_label_0` = next performance date `DD.MM.YYYY`, `_1` = category, `_2` = venue,
`_3` = time `HH:MM`.
Staatsoper items also get `g:additional_image_link`: up to 20 comma-separated URLs of the
other photos from the production's "Impression" gallery (Meta's format for this field).

- **In Meta:** Catalog → Data sources → Data feed → scheduled feed, use the XML URL,
  and set a daily fetch (after ~03:00 Berlin).
- **In [meta-supplement-feed](https://github.com/HolgerKurtz/meta-supplement-feed):** enter the
  XML URL as `source_xml_url`. `custom_label_0` is the date that its `custom_number_0` /
  `expiration_date` rules read.
- Staatsoper images are only available as WebP, and Meta doesn't accept WebP. They are
  served through the free proxy [wsrv.nl](https://wsrv.nl), which converts them to JPEG on
  the fly; nothing is hosted here. If wsrv.nl goes away, change `jpeg_url()` in
  `pagefeed.py` (e.g. to Cloudinary's fetch URL).
- Image, gallery photos and description come from each production page. They are cached in
  `feeds/<client>-details.json` and re-fetched after 30 days, so only new pages cause requests.
- Step-by-step diagram of where each Staatsoper field comes from:
  [`docs/staatsoper-feed-flow.html`](docs/staatsoper-feed-flow.html) (open it in a browser).
- Bergson: one item per event (each date has its own page). Image = the original of the list
  thumbnail (`/file/show/...`), converted to a 1200 px JPEG through wsrv.nl, because some
  originals are GIFs or larger than Meta's 8 MB limit. The event pages have no gallery, so
  Bergson items have no `additional_image_link`. Price = lowest value of the price
  range in the event's ticket shop page (tickets.bergson.com); a range starting at € 0
  (free companion ticket) becomes `SOCIAL_PRICE` (28.00 EUR). "Ausverkauft!" there also
  makes the event out of stock. Description and price are cached like the Staatsoper
  details (30 days), so only new events cause requests (event page + ticket page).
- Pages without an image (side events such as tours, school programmes and talks) or without a
  listed price (presale not open yet) are left out of the product feed. They stay in the page feed.

## Load on the client sites

- Scraped at most once per Berlin day. The extra cron triggers only exist because GitHub
  starts scheduled runs late, and they exit early if a run already succeeded that day.
  Manual runs always scrape.
- `fetch()` requests gzip (the Staatsoper calendar goes from 3.1 MB to 150 KB), sends one
  request at a time with a 1 s pause, and identifies itself as
  `event-pagefeeds/1.0 (+https://github.com/HolgerKurtz/event-pagefeeds)`.
  New clients get this automatically by using `fetch()`.
- The sites send no ETag/Last-Modified, so conditional requests aren't possible.

## Adding a client

Create `clients/<name>.py` with:

```python
from pagefeed import FeedError, fetch

MIN_EVENTS = 50      # fewer means the site or the parser broke

def scrape():
    html = fetch("https://example.com/events")
    rows = {}        # {absolute page URL: [label, ...]}
    ...
    return n_events, rows
```

For a product feed as well, return a list of item dicts instead of `rows` and set `BRAND`
and `LINK` (see `clients/staatsoper.py` and the docstring of `pagefeed.py`).

Run `python3 pagefeed.py <name>`, commit the module and the new `feeds/<name>.csv`.
The workflow picks it up automatically.

## When it breaks

A client's run fails (and its last good CSV stays live) if:
- the site is unreachable / returns an error,
- fewer than `MIN_EVENTS` events are found,
- no URL has a label,
- the feed would shrink by more than 50 % compared with the last run,
- (product feed) too many items lack a price or an image (`MAX_INCOMPLETE`), or the
  first converted image doesn't come back as `200 image/jpeg`.

The other clients are still updated; the workflow run is marked failed, which
triggers a GitHub email. If a big drop is legitimate, delete `feeds/<client>.csv`
and re-run the workflow manually.
