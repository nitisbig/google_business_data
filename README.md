# Google Business Data Scraper

A small command-line tool that scrapes business listings from **Google Maps**
for a given search query and appends the results — **name, rating, reviews,
phone, website, emails, address** — to a CSV file.

For each business that has a website, the tool also **visits that site and pulls
contact email(s)** — scanning the homepage and, if needed, one Contact/About
page. This is on by default (use `--no-emails` to skip).

It uses a real Chromium browser (via Playwright) because Google renders its data
with JavaScript and blocks plain HTTP clients. No AI is used — the flow is a
deterministic script with stable selectors.

> ⚠️ Scraping Google is against Google's Terms of Service. For production use,
> consider the official (paid) **Google Places API** instead. Use this tool
> responsibly, at low volume, for learning/personal purposes.

## Setup

```bash
# 1. install python deps
pip install -r requirements.txt

# 2. download the Chromium browser Playwright drives
playwright install chromium
```

## Usage

```bash
# basic: scrape "lawyer in new york" -> lawyer_data.csv
python scrape.py "lawyer in new york"

# choose output file and how many businesses to collect
python scrape.py "dentist in chicago" --out dentist_data.csv --max 60

# run without a visible window (higher block risk, can't solve CAPTCHAs)
python scrape.py "plumber in austin" --headless

# resume an interrupted run -- skip businesses already visited for this query
python scrape.py "lawyer in new york" --resume

# skip email extraction -> much faster, only Google Maps data
python scrape.py "lawyer in new york" --no-emails
```

### Options

| Flag              | Default            | Description                                              |
|-------------------|--------------------|---------------------------------------------------------|
| `query`           | *(required)*       | Search text, e.g. `"lawyer in new york"`                |
| `--out`           | `lawyer_data.csv`  | CSV file to append results to                           |
| `--max`           | `40`               | Max number of businesses to collect                     |
| `--headless`      | off (visible)      | Run the browser without a visible window                |
| `--resume`        | off                | Skip listings already visited for this query (see below)|
| `--no-emails`     | off (emails on)    | Don't visit business websites to extract emails         |
| `--email-timeout` | `15000`            | Per-site timeout in ms when extracting emails           |

### Email extraction

By default, for every business that lists a website, the tool opens that site in
a second browser tab and looks for contact email(s): it scans the homepage and,
if none are found, follows one **Contact/About** link and scans that page too.
Multiple emails are joined with `;` in the `emails` column. Images/fonts are
blocked on this tab to keep it fast. Use `--no-emails` to turn this off.

> **New `emails` column:** a CSV created before this feature won't have the
> `emails` header. Delete the old file (the tool recreates it with the new
> columns) or new rows will misalign — the tool prints a warning if it detects a
> mismatched header.

### Resuming an interrupted run

Every run records the place URLs it has visited (per query) in a sidecar file
next to your CSV: `<out>.progress.json` (e.g. `lawyer_data.csv.progress.json`).
This file is updated after **each** business, so it survives a crash, a CAPTCHA,
or a `Ctrl+C`.

Pass `--resume` to pick up where you left off — already-visited listings are
skipped *without even opening them*, so the run is much faster:

```bash
python scrape.py "lawyer in new york" --max 200            # first pass (interrupt any time)
python scrape.py "lawyer in new york" --max 200 --resume   # continues, skips what's done
```

To start a query over from scratch, delete its progress file (or the whole
`.progress.json`). Note: resume tracking is **per query string**, so keep the
query text identical between runs.

## Notes

- The browser profile is stored in `.gmaps_profile/` so cookies/consent persist
  between runs (keeps Google happier and avoids re-clicking the consent screen).
- Results are written **incrementally** — if the run is interrupted, whatever was
  already collected is saved.
- Re-running the same query **does not** create duplicate rows (dedupe by
  name + address).
- If extracted fields start coming back blank, Google likely changed its page
  layout; the selectors in `scrape.py` (`extract_business`) will need updating.
