# Event page feeds for Google Ads (DSA)

Daily Google Ads page feeds with all upcoming events, one CSV per client.

| Client | Source | Labels | Feed URL |
|---|---|---|---|
| `bergson` | bergson.com/events/filter | genre slugs (`klassik;jazz`) | https://raw.githubusercontent.com/HolgerKurtz/event-pagefeeds/main/feeds/bergson.csv |
| `staatsoper` | die-hamburgische-staatsoper.de/de/kalender (only pages with ≥1 future, not sold-out performance) | `oper` / `ballett` / `konzert` / `sonstiges` | https://raw.githubusercontent.com/HolgerKurtz/event-pagefeeds/main/feeds/staatsoper.csv |

- `pagefeed.py` – shared runner: fetches, sanity-checks and writes `feeds/<client>.csv`
  (`Page URL`, `Custom label`). Python stdlib only.
- `clients/<client>.py` – one scraper per website.
- `.github/workflows/update-pagefeed.yml` – runs daily, commits changed CSVs.

Run locally: `python3 pagefeed.py` (all clients) or `python3 pagefeed.py staatsoper`.

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

Run `python3 pagefeed.py <name>`, commit the module and the new `feeds/<name>.csv`.
The workflow picks it up automatically.

## When it breaks

A client's run fails (and its last good CSV stays live) if:
- the site is unreachable / returns an error,
- fewer than `MIN_EVENTS` events are found,
- no URL has a label,
- the feed would shrink by more than 50 % compared with the last run.

The other clients are still updated; the workflow run is marked failed, which
triggers a GitHub email. If a big drop is legitimate, delete `feeds/<client>.csv`
and re-run the workflow manually.
