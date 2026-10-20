# Bergson DSA page feed

Daily Google Ads page feed with all upcoming events from bergson.com.

- `pagefeed.py` – scrapes `https://bergson.com/events/filter`, writes `pagefeed.csv`
  (`Page URL`, `Custom label` = genre slugs like `klassik;jazz`). Python stdlib only.
- `.github/workflows/update-pagefeed.yml` – runs daily, commits the CSV if it changed.

Feed URL for Google Ads (HTTPS, no auth):
`https://raw.githubusercontent.com/<user>/<repo>/main/pagefeed.csv`

## When it breaks

The script exits with an error (and the last good CSV stays live) if:
- the site is unreachable / returns an error,
- fewer than 50 events are found,
- no event has a genre label,
- the feed would shrink by more than 50 % compared with the last run.

A failed run triggers a GitHub email. If a big drop is legitimate, delete
`pagefeed.csv` and re-run the workflow manually.
