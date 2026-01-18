# RSS Feed End-to-End Flow

This document describes the current end-to-end flow of `rss_feed.py` and is intended to be updated as the code evolves.

## 1) Configuration Load
- Load `rss_feed/config.json`.
- Read:
  - `rss_urls` (NSE RSS feeds).
  - `external_feeds` (BSE, Moneycontrol, EconomicTimes, LiveMint, BQPrime).
  - `nse_pages` (non‑RSS NSE pages, currently corporate filings announcements).

## 2) Output Setup
- Ensure output directory exists: `rss_feed/data/`.
- Excel output target: `rss_feed/data/rss_feed.xlsx`.
- Stock list file (for optional filtering): `rss_feed/data/Stock Industry_Sector list.xlsx`.

## 3) Fetch NSE RSS Feeds
- Create a `requests.Session`.
- Prime NSE cookies by visiting `https://www.nseindia.com`.
- For each URL in `rss_urls`:
  - `GET` with NSE headers.
  - Parse with `feedparser`.
  - Build rows with fields:
    - `Source` (RSS filename without `.xml`)
    - `Published`, `Title`, `Link`, `Description`, `FetchedAt`
  - Record stats per feed.

## 4) Fetch External RSS Feeds
- For each `external_feeds` entry:
  - `GET` with generic headers.
  - Parse with `feedparser`.
  - Build rows with fields:
    - `Source` (e.g., `BSE`, `Moneycontrol`)
    - `Published`, `Title`, `Link`, `Description`, `FetchedAt`
  - Record stats per feed.

## 5) Fetch NSE Non‑RSS Pages
- Current: **Corporate Filings Announcements**
  - Page URL: `https://www.nseindia.com/companies-listing/corporate-filings-announcements`
  - API URL: `https://www.nseindia.com/api/corporate-announcements?index=equities`
  - Flow:
    - Prime cookies (`nseindia.com` + page URL).
    - Fetch base API (typically 20 records):
      - `Source = NSE_Corporate_Filings`
    - Fetch range API (last `range_days`):
      - `Source = NSE_Corporate_Filings_All`
    - Build rows with fields:
      - `Published`, `Title`, `Description`, `Symbol`, `Company`, `ISIN`, `Industry`
      - `Attachment` (PDF link), `XBRL_Link` (when `hasXbrl=True`)
      - `FetchedAt`

## 6) Combine + Deduplicate
- Combine NSE RSS + External RSS + NSE Non‑RSS rows.
- Create a single `DataFrame` and enforce column order:
  - `Source`, `Published`, `Title`, `Link`, `Description`, `FetchedAt`, plus any extra columns.
- If Excel already exists, append new rows and drop duplicates by `Link`.

## 7) Filter to Recent Window
- Keep records within **last 30 days** per source.
- Use `Published` date if available; fallback to date extracted from `Description`.
- Records without a parsable date are kept as a fallback.

## 8) Summary Output
- Print a summary listing feeds with entries and feeds with errors/no entries.

## 9) Optional Stock Filtering (Interactive)
- If running interactively, prompt for a stock list file:
  - `.txt`, `.csv`, or `.xlsx`
- Load stock symbol → company name mapping from `Stock Industry_Sector list.xlsx`.
- Build a per‑row **search blob** by concatenating all column values.
- Match symbols/company names against this blob (fuzzy matching).
  - Short symbols (length ≤ 3) still require **word‑boundary exact matches**.
- If matches exist:
  - Generate `rss_feed/data/stock_matches.html`.
  - Open in browser if interactive.
  - Highlight configured keywords (from `config.json`) inside Description/Link in the HTML output.

## 10) UI Search (Single Stock, Live)
- `stock_matches.html` includes a **Search Excel** box.
- The box calls a local server that reuses the same Python search logic:
  - Start it with: `py rss_feed_server.py`
  - Endpoint: `http://localhost:5005/search?stock=SYMBOL`
- Server reads `rss_feed/data/rss_feed.xlsx` and returns JSON results.
- This keeps UI search behavior consistent with `search_stocks_in_dataframe`, including exceptions like the BSE rule.

## 11) Save to Excel
- Save final DataFrame to `rss_feed/data/rss_feed.xlsx`.
- If Excel is open, saving will fail with a permission error.

---

## Notes
- `BQPrime` currently returns 404 from its feed URL (logged as an error).
- `NSE_Corporate_Filings_All` pulls the larger date-range dataset via API params.
- `XBRL_Link` is built as `https://www.nseindia.com/api/xbrl/{seq_id}` when `hasXbrl=True`.
