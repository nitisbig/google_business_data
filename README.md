# Google Business Data Scraper

A small command-line tool that scrapes business listings from **Google Maps**
for a given search query and appends the results — **name, rating, reviews,
phone, website, address** — to a CSV file.

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
```

### Options

| Flag         | Default            | Description                                   |
|--------------|--------------------|-----------------------------------------------|
| `query`      | *(required)*       | Search text, e.g. `"lawyer in new york"`      |
| `--out`      | `lawyer_data.csv`  | CSV file to append results to                 |
| `--max`      | `40`               | Max number of businesses to collect           |
| `--headless` | off (visible)      | Run the browser without a visible window      |

## Notes

- The browser profile is stored in `.gmaps_profile/` so cookies/consent persist
  between runs (keeps Google happier and avoids re-clicking the consent screen).
- Results are written **incrementally** — if the run is interrupted, whatever was
  already collected is saved.
- Re-running the same query **does not** create duplicate rows (dedupe by
  name + address).
- If extracted fields start coming back blank, Google likely changed its page
  layout; the selectors in `scrape.py` (`extract_business`) will need updating.
