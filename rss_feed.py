import requests
import feedparser
import pandas as pd
import os
import json
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone
import re
from dateutil import parser as date_parser
import webbrowser
from rapidfuzz import fuzz, process
import sys

# =====================
# CONFIG
# =====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DATA_DIR = os.path.join(BASE_DIR, "data")
EXCEL_FILE = os.path.join(DATA_DIR, "rss_feed.xlsx")
STOCK_INDUSTRY_FILE = os.path.join(DATA_DIR, "Stock Industry_Sector list.xlsx")

# Load config
with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)
RSS_URLS = config.get("rss_urls", [])
EXTERNAL_FEEDS = config.get("external_feeds", {})
NSE_PAGES = config.get("nse_pages", {})
CATALYST_KEYWORDS = config.get("catalyst_keywords", {})

def normalize_text(text):
    """Lowercase and strip punctuation, collapse whitespace."""
    if text is None:
        return ""
    s = str(text).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = " ".join(s.split())
    return s

def _build_phrase_map(phrases):
    phrase_map = {}
    for p in phrases or []:
        p_norm = normalize_text(p)
        if p_norm:
            phrase_map[p_norm] = p
    return phrase_map

def _build_keyword_maps(cfg):
    universal = {}
    sectors = {}
    filters = {}
    highlight = []

    if not cfg:
        return universal, sectors, filters, highlight

    for cat, terms in (cfg.get("universal") or {}).items():
        universal[cat] = _build_phrase_map(terms)
        highlight.extend(terms or [])

    for sector, groups in (cfg.get("sectors") or {}).items():
        sectors[sector] = {}
        for cat, terms in (groups or {}).items():
            sectors[sector][cat] = _build_phrase_map(terms)
            highlight.extend(terms or [])

    for cat, terms in (cfg.get("filters") or {}).items():
        filters[cat] = _build_phrase_map(terms)
        highlight.extend(terms or [])

    return universal, sectors, filters, highlight

UNIVERSAL_KW, SECTOR_KW, FILTER_KW, HIGHLIGHT_KEYWORDS = _build_keyword_maps(CATALYST_KEYWORDS)
NEGATIVE_KEYWORDS = list((CATALYST_KEYWORDS.get("filters") or {}).get("negative", []))

# Search exceptions for specific symbols
SEARCH_EXCEPTIONS = {
    # For stock symbol "BSE", ignore BSE-source rows to avoid a flood of obvious matches.
    "BSE": {
        "source_equals": "BSE",
        "exclude_columns": ["Source", "Link", "Attachment", "XBRL_Link"],
        "strip_urls": True,
        "skip_source_rows": True,
    }
}

# Ensure directory exists
os.makedirs(os.path.dirname(EXCEL_FILE), exist_ok=True)

# =====================
# DATE PARSING FUNCTIONS
# =====================
def parse_date_from_published(published_str):
    """Parse date from Published column"""
    if pd.isna(published_str) or published_str == "":
        return None
    try:
        # Try parsing with dateutil parser (handles various formats)
        return date_parser.parse(str(published_str))
    except:
        return None

def extract_date_from_description(description_str):
    """Extract date from Description column using regex patterns"""
    if pd.isna(description_str) or description_str == "":
        return None
    
    description = str(description_str)
    
    # Common date patterns in descriptions
    date_patterns = [
        r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}',  # DD/MM/YYYY or DD-MM-YYYY
        r'\d{4}[/-]\d{1,2}[/-]\d{1,2}',    # YYYY/MM/DD or YYYY-MM-DD
        r'\d{1,2}\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{2,4}',  # DD Mon YYYY
        r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{2,4}',  # Mon DD, YYYY
    ]
    
    for pattern in date_patterns:
        matches = re.findall(pattern, description, re.IGNORECASE)
        if matches:
            try:
                # Try to parse the first match
                date_str = re.search(pattern, description, re.IGNORECASE).group(0)
                return date_parser.parse(date_str, fuzzy=True)
            except:
                continue
    
    return None

def get_record_date(row):
    """Get date from Published column, or fallback to Description"""
    # Try Published column first
    date = parse_date_from_published(row.get('Published', ''))
    if date:
        return date
    
    # Fallback to Description column
    date = extract_date_from_description(row.get('Description', ''))
    return date

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "application/rss+xml,application/xml,text/xml,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# Headers for external feeds (simpler, no NSE-specific referer)
EXTERNAL_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# Headers for NSE API calls (page-based data)
NSE_API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# =====================
# STOCK LIST FUNCTIONS
# =====================
def normalize_company_name(name):
    """Normalize company name for fuzzy matching by handling common variations"""
    if pd.isna(name) or name == "":
        return ""
    
    name = str(name).strip().upper()
    
    # Dictionary of common company name variations
    replacements = {
        'LIMITED': 'LTD',
        'INCORPORATED': 'INC',
        'CORPORATION': 'CORP',
        'PRIVATE LIMITED': 'PVT LTD',
        'PVT. LTD.': 'PVT LTD',
        'PRIVATE LTD.': 'PVT LTD',
        'PRIVATE LTD': 'PVT LTD',
        'LTD.': 'LTD',
        'INC.': 'INC',
        'CORP.': 'CORP',
        '&': 'AND',
        ' + ': ' AND ',
        '  ': ' ',  # Multiple spaces to single space
    }
    
    # Apply replacements
    for old, new in replacements.items():
        name = name.replace(old, new)
    
    # Remove common punctuation
    name = re.sub(r'[.,;:()\[\]{}]', '', name)
    
    # Remove extra spaces
    name = ' '.join(name.split())
    
    return name

def load_stock_company_mapping():
    """Load stock symbol to company name mapping from Stock Industry_Sector list.xlsx"""
    symbol_to_company = {}
    
    if not os.path.exists(STOCK_INDUSTRY_FILE):
        print(f"Warning: Stock Industry_Sector list.xlsx not found at {STOCK_INDUSTRY_FILE}")
        return symbol_to_company
    
    try:
        df = pd.read_excel(STOCK_INDUSTRY_FILE)
        
        # Get symbol column (first column, index 0) and company name column (fourth column, index 3)
        if len(df.columns) >= 4:
            symbol_col = df.iloc[:, 0]  # First column
            company_col = df.iloc[:, 3]  # Fourth column
            
            for idx, symbol in enumerate(symbol_col):
                if pd.notna(symbol) and pd.notna(company_col.iloc[idx]):
                    symbol_str = str(symbol).strip().upper()
                    company_str = str(company_col.iloc[idx]).strip()
                    if symbol_str and company_str:
                        symbol_to_company[symbol_str] = company_str
        else:
            print(f"Warning: Stock Industry_Sector list.xlsx doesn't have enough columns")
        
        print(f"Loaded {len(symbol_to_company)} stock symbol to company name mappings")
        return symbol_to_company
    except Exception as e:
        print(f"Error loading Stock Industry_Sector list.xlsx: {str(e)}")
        return symbol_to_company

def parse_stock_format(stock_string):
    """Parse stock format like 'NSE:CRAFTSMAN' or 'NYSE:HCC' and extract stock symbol"""
    stock_string = stock_string.strip()
    if ':' in stock_string:
        # Split by colon and take the part after the colon (the stock symbol)
        parts = stock_string.split(':', 1)
        if len(parts) == 2:
            return parts[1].strip().upper()
    # If no colon, return as is (already a stock symbol)
    return stock_string.upper()

def read_stocks_list(file_path):
    """Read stocks list from file (supports .txt, .csv, .xlsx)"""
    stocks = []
    try:
        if file_path.endswith('.txt'):
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                # Check if content is in format like "NSE:CRAFTSMAN,NYSE:HCC,NSE:LUMAXIND"
                if ',' in content and ':' in content:
                    # Parse comma-separated format with prefixes
                    stock_items = [item.strip() for item in content.split(',') if item.strip()]
                    stocks = [parse_stock_format(item) for item in stock_items]
                elif ',' in content:
                    # Comma-separated format without prefixes (e.g., "IDBI, UNIONBANK, SAWALIYA")
                    stock_items = [item.strip() for item in content.split(',') if item.strip()]
                    stocks = [parse_stock_format(item) for item in stock_items]
                else:
                    # Regular line-by-line format
                    stocks = [parse_stock_format(line.strip()) for line in content.split('\n') if line.strip()]
        elif file_path.endswith('.csv'):
            df = pd.read_csv(file_path)
            # Try common column names
            for col in ['Stock', 'Stocks', 'Symbol', 'Symbols', 'Name', 'Company']:
                if col in df.columns:
                    stocks = [parse_stock_format(str(s)) for s in df[col].dropna() if str(s).strip()]
                    break
            if not stocks:
                # If no matching column, use first column
                stocks = [parse_stock_format(str(s)) for s in df.iloc[:, 0].dropna() if str(s).strip()]
        elif file_path.endswith('.xlsx') or file_path.endswith('.xls'):
            df = pd.read_excel(file_path)
            # Try common column names
            for col in ['Stock', 'Stocks', 'Symbol', 'Symbols', 'Name', 'Company']:
                if col in df.columns:
                    stocks = [parse_stock_format(str(s)) for s in df[col].dropna() if str(s).strip()]
                    break
            if not stocks:
                # If no matching column, use first column
                stocks = [parse_stock_format(str(s)) for s in df.iloc[:, 0].dropna() if str(s).strip()]
        else:
            print(f"Unsupported file format. Please use .txt, .csv, or .xlsx")
            return []
        
        # Remove duplicates and empty strings
        stocks = list(set([s for s in stocks if s]))
        return stocks
    except Exception as e:
        print(f"Error reading stocks list file: {str(e)}")
        return []

def fuzzy_match_company_name(search_name, text, threshold=85):
    """Check if normalized company name matches in text using fuzzy matching"""
    if not search_name or not text:
        return False
    
    normalized_search = normalize_company_name(search_name)
    normalized_text = normalize_company_name(text)
    
    if not normalized_search or not normalized_text:
        return False
    
    # Extract significant words (remove common words that appear in many company names)
    # Expanded common words list to include INDUSTRIES, ENGINEERING, INDIA, etc.
    common_words = {
        'LTD', 'INC', 'CORP', 'PVT', 'AND', 'THE', 'OF', 'IN', 'FOR', 'TO', 
        'INDUSTRIES', 'INDUSTRY', 'ENGINEERING', 'AUTOMATION', 'SYSTEMS',
        'TECHNOLOGIES', 'TECHNOLOGY', 'SOLUTIONS', 'SERVICES', 'GROUP',
        'GLOBAL', 'INTERNATIONAL', 'COMPANY', 'COMPANIES',
        'INDIA', 'OVERSEAS', 'FINANCE', 'FINANCIAL', 'BANK', 'BANKING'
    }
    
    # Extract significant words, handling numeric prefixes (e.g., "3M" stays as one word)
    def extract_significant_words(text):
        words = []
        for word in text.split():
            # Skip common words
            if word in common_words:
                continue
            # Keep words longer than 2 characters OR words with numbers (like "3M")
            if len(word) > 2 or any(char.isdigit() for char in word):
                words.append(word)
        return words
    
    search_words = extract_significant_words(normalized_search)
    text_words = extract_significant_words(normalized_text)
    
    # If no significant words after removing common words, fall back to exact match
    if not search_words:
        # Only match if the full normalized search name appears in text (exact match)
        return normalized_search in normalized_text
    
    # Require at least 2 significant words for fuzzy matching (stricter requirement)
    # Single-word company names need exact match only (no fuzzy matching)
    if len(search_words) == 1:
        # For single significant word, require exact substring match of full normalized name
        # This prevents false matches like "3M" matching companies with "3M" as substring
        if normalized_search in normalized_text:
            return True
        # Also check if the word appears as complete word with word boundaries
        # AND the full normalized search name must be very similar to the text
        word = search_words[0]
        if re.search(r'\b' + re.escape(word) + r'\b', normalized_text, re.IGNORECASE):
            # For single word, require the full normalized name to be in the text
            # OR require very high similarity (98%+) - stricter than before
            if normalized_search in normalized_text:
                return True
            ratio = fuzz.ratio(normalized_search, normalized_text)
            if ratio >= 98:  # Very strict threshold for single-word matches
                return True
        return False
    
    # For multiple words, require that ALL significant words from search appear in text
    # This ensures the unique part of the company name matches
    all_words_match = all(word in text_words for word in search_words)
    if all_words_match and len(search_words) >= 2:
        return True
    
    # If not all words match, require at least 90% of significant words to match
    # AND require that the first significant word matches (company name usually starts with unique word)
    matching_words = sum(1 for word in search_words if word in text_words)
    first_word_match = search_words[0] in text_words if search_words else False
    
    # Stricter: require 90% match (was 80%) AND first word must match
    if matching_words >= len(search_words) * 0.9 and first_word_match and len(search_words) >= 2:
        return True
    
    # Use token-based fuzzy matching with higher threshold (handles word order variations)
    # Only use this if we have multiple significant words (at least 2)
    if len(search_words) >= 2:
        ratio = fuzz.token_sort_ratio(normalized_search, normalized_text)
        # Increased threshold to 90% (was 85%) for stricter matching
        if ratio >= 90:
            return True
    
    # Use partial ratio with very high threshold - only for very close matches
    partial_ratio = fuzz.partial_ratio(normalized_search, normalized_text)
    if partial_ratio >= 95:  # Very high threshold (was 90%) for partial matches
        return True
    
    return False

def search_stocks_in_dataframe(df, stocks_list, symbol_to_company_map):
    """Search for stocks across all column values using both symbol and company name with fuzzy matching"""
    if df.empty or not stocks_list:
        return pd.DataFrame()
    
    # Create a copy to avoid modifying original
    df_search = df.copy()
    
    # Build a single search blob per row by concatenating all column values
    df_search['Row_Blob'] = df_search.astype(str).agg(' '.join, axis=1)
    df_search['Row_Blob_Upper'] = df_search['Row_Blob'].str.upper()

    # Precompute alternate blob for exception cases (e.g., BSE symbol on BSE source rows)
    def build_filtered_blob(row, exclude_cols, strip_urls=False):
        values = []
        for col, val in row.items():
            if col in exclude_cols:
                continue
            values.append(str(val))
        blob = " ".join(values)
        if strip_urls:
            # Remove URL-like substrings to avoid matches from link text
            blob = re.sub(r'https?://\S+|www\.\S+', ' ', blob)
        return blob

    if SEARCH_EXCEPTIONS:
        for symbol, rule in SEARCH_EXCEPTIONS.items():
            source_match = rule.get("source_equals")
            exclude_cols = set(rule.get("exclude_columns", []))
            strip_urls = bool(rule.get("strip_urls"))
            if source_match and exclude_cols:
                mask_source = df_search["Source"].astype(str) == source_match
                df_search.loc[mask_source, f"Row_Blob_{symbol}"] = df_search.loc[mask_source].apply(
                    lambda r: build_filtered_blob(r, exclude_cols, strip_urls=strip_urls), axis=1
                )
                df_search.loc[mask_source, f"Row_Blob_{symbol}_Upper"] = df_search.loc[mask_source, f"Row_Blob_{symbol}"].str.upper()
    
    # Find rows where any stock symbol or company name appears
    mask = pd.Series([False] * len(df_search))
    matched_map = {idx: set() for idx in df_search.index}
    
    for stock_symbol in stocks_list:
        # Get company name for this symbol
        company_name = symbol_to_company_map.get(stock_symbol, None)
        
        # Pick blob based on exceptions (fallback to full row blob)
        blob_col = "Row_Blob"
        blob_upper_col = "Row_Blob_Upper"
        exception = SEARCH_EXCEPTIONS.get(stock_symbol)
        if exception and exception.get("source_equals"):
            blob_col = f"Row_Blob_{stock_symbol}"
            blob_upper_col = f"Row_Blob_{stock_symbol}_Upper"
            if blob_col not in df_search.columns:
                blob_col = "Row_Blob"
                blob_upper_col = "Row_Blob_Upper"

        # Search for symbol in row blob (strict rules for short symbols)
        if len(stock_symbol) <= 3:
            # Use word boundary regex for short symbols to prevent false matches
            # e.g., "LT" should match "LT Limited" but not "Limited" or "KELLTON"
            symbol_pattern = r'\b' + re.escape(stock_symbol) + r'\b'
            symbol_mask = df_search[blob_upper_col].str.contains(symbol_pattern, na=False, regex=True)
        else:
            # For longer symbols, substring matching is acceptable
            symbol_mask = df_search[blob_upper_col].str.contains(stock_symbol, na=False, regex=False)
        
        # Search for company name using fuzzy matching across the full row blob
        company_mask = pd.Series([False] * len(df_search))
        if company_name:
            for idx in df_search.index:
                row_match = fuzzy_match_company_name(company_name, df_search.loc[idx, blob_col])
                if row_match:
                    company_mask.loc[idx] = True
        
        combined_mask = symbol_mask | company_mask

        # Apply exception row skipping (e.g., skip matching symbol BSE against Source=BSE rows entirely)
        if exception and exception.get("skip_source_rows") and exception.get("source_equals"):
            skip_mask = df_search["Source"].astype(str) == str(exception["source_equals"])
            combined_mask = combined_mask & (~skip_mask)

        mask |= combined_mask

        # Track which stock matched which rows
        for idx in df_search.index[combined_mask]:
            matched_map[idx].add(stock_symbol)
    
    # Filter matching rows and attach matched stock list
    matched_df = df_search[mask].copy()
    matched_df["Matched_Stock"] = matched_df.index.map(
        lambda i: ", ".join(sorted(matched_map.get(i, set())))
    )
    
    # Keyword categorization for matched rows
    def detect_sectors(text_norm, industry_norm):
        sectors_found = []
        for sector_key in SECTOR_KW.keys():
            sector_norm = normalize_text(sector_key)
            if not sector_norm:
                continue
            # Short sector keys use word boundary matching
            if len(sector_norm) <= 3:
                pattern = r"\b" + re.escape(sector_norm) + r"\b"
                if re.search(pattern, text_norm) or re.search(pattern, industry_norm):
                    sectors_found.append(sector_key)
            else:
                if sector_norm in text_norm or sector_norm in industry_norm:
                    sectors_found.append(sector_key)
        return sectors_found

    def find_phrase_matches(text_norm, phrase_map):
        matches = []
        if not phrase_map:
            return matches
        padded_text = f" {text_norm} "
        for p_norm, p_raw in phrase_map.items():
            if f" {p_norm} " in padded_text:
                matches.append(p_raw)
        return matches

    def format_category_matches(category_map, text_norm):
        parts = []
        for cat, phrases in category_map.items():
            matches = find_phrase_matches(text_norm, phrases)
            if matches:
                parts.append(f"{cat}: {', '.join(sorted(set(matches)))}")
        return "; ".join(parts)

    kw_universal = []
    kw_sector = []
    kw_filters = []
    has_negative = []

    for _, row in matched_df.iterrows():
        row_text = f"{row.get('Title','')} {row.get('Description','')} {row.get('Link','')} {row.get('Attachment','')} {row.get('XBRL_Link','')}"
        text_norm = normalize_text(row_text)
        industry_norm = normalize_text(row.get("Industry", ""))

        # Universal categories
        kw_universal.append(format_category_matches(UNIVERSAL_KW, text_norm))

        # Sector categories
        sector_matches = []
        for sector_key in detect_sectors(text_norm, industry_norm):
            sector_map = SECTOR_KW.get(sector_key, {})
            sector_hit = format_category_matches(sector_map, text_norm)
            if sector_hit:
                sector_matches.append(f"{sector_key}: {sector_hit}")
        kw_sector.append("; ".join(sector_matches))

        # Filters / negative keywords
        kw_filters.append(format_category_matches(FILTER_KW, text_norm))
        has_negative.append(bool(format_category_matches(FILTER_KW, text_norm)))

    matched_df["KW_Universal"] = kw_universal
    matched_df["KW_Sector"] = kw_sector
    matched_df["KW_Filters"] = kw_filters
    matched_df["Has_Negative"] = has_negative

    # Drop temporary columns
    drop_cols = ['Row_Blob', 'Row_Blob_Upper']
    for symbol in SEARCH_EXCEPTIONS.keys():
        drop_cols.extend([f"Row_Blob_{symbol}", f"Row_Blob_{symbol}_Upper"])
    matched_df = matched_df.drop(columns=drop_cols, errors='ignore')
    
    return matched_df

def select_link_display(row):
    source = str(row.get("Source", ""))
    if source.startswith("NSE_Corporate_Filings"):
        return row.get("Attachment", "") or row.get("Link", "")
    return row.get("Link", "")


def generate_html_page(df, stocks_list, output_path, df_full=None):
    """Generate HTML page with stock matches and clickable links"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>NSE RSS Feed - Stock Matches</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                margin: 16px;
                background-color: #f5f5f5;
            }
            .info {
                background-color: #e7f3ff;
                padding: 10px 12px;
                border-radius: 5px;
                margin-bottom: 12px;
                font-size: 12px;
            }
            .search-bar {
                display: flex;
                gap: 8px;
                justify-content: flex-end;
                margin-bottom: 12px;
            }
            .search-bar input {
                padding: 6px 8px;
                border: 1px solid #ccc;
                border-radius: 4px;
                font-size: 12px;
                min-width: 220px;
            }
            .search-bar button {
                padding: 6px 10px;
                border: 1px solid #ccc;
                border-radius: 4px;
                background: #f8f8f8;
                font-size: 12px;
                cursor: pointer;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                background-color: white;
                box-shadow: 0 1px 3px rgba(0,0,0,0.08);
                table-layout: fixed;
            }
            th {
                background-color: #4CAF50;
                color: white;
                padding: 8px 10px;
                text-align: left;
                font-weight: 600;
                font-size: 12px;
                position: sticky;
                top: 0;
                z-index: 1;
            }
            td {
                padding: 6px 10px;
                border-bottom: 1px solid #eee;
                font-size: 12px;
                line-height: 1.3;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                vertical-align: top;
            }
            /* Wrap long text for Description and Link columns */
            td.col-description,
            td.col-link {
                white-space: normal;
                overflow: visible;
                text-overflow: clip;
                word-break: break-word;
                overflow-wrap: anywhere;
            }
            tr:hover {
                background-color: #f5f5f5;
            }
            tr.kw-row td {
                background-color: #fff8d6;
            }
            tr.neg-row td {
                background-color: #ffe3e3;
            }
            .kw {
                background: #ffeb3b;
                padding: 0 2px;
                border-radius: 2px;
                font-weight: 600;
            }
            a {
                color: #0066cc;
                text-decoration: none;
            }
            a:hover {
                text-decoration: underline;
            }
            .no-results {
                text-align: center;
                padding: 32px;
                color: #666;
                font-size: 14px;
            }
            .col-stock { width: 6%; }
            .col-source { width: 10%; }
            .col-published { width: 11%; }
            .col-description { width: 49%; }
            .col-link { width: 24%; }
        </style>
    </head>
    <body>
        <div class="info">
            <strong>Stocks Searched:</strong> <span id="stocksSearched">{stocks_searched}</span><br>
            <strong>Total Matches Found:</strong> <span id="totalMatches">{total_matches}</span>
        </div>
        <div class="search-bar">
            <input id="stockSearchInput" type="text" placeholder="Filter current results (e.g., TCS)" />
            <button id="stockSearchBtn" type="button">Filter</button>
            <button id="stockResetBtn" type="button">Reset</button>
            <span style="flex:1"></span>
            <input id="excelSearchInput" type="text" placeholder="Search Excel by stock (e.g., TCS)" />
            <button id="excelSearchBtn" type="button">Search Excel</button>
        </div>
    """
    
    if df.empty:
        html_content += """
        <div class="no-results">
            No matches found for the specified stocks.
        </div>
        """
    else:
        df_display = df.copy()

        df_display["Link_Display"] = df_display.apply(select_link_display, axis=1)

        # Define compact column order for UI
        display_cols = ["Matched_Stock", "Source", "Published", "Description", "Link_Display"]

        # Sort by matched stock name (alphabetical)
        df_display = df_display.sort_values(by=["Matched_Stock"], kind="stable")

        # Keyword highlight (optional, driven by config)
        keywords = [k.strip() for k in (HIGHLIGHT_KEYWORDS or []) if str(k).strip()]
        keywords_sorted = sorted(set(keywords), key=len, reverse=True)
        keyword_pattern = None
        if keywords_sorted:
            keyword_pattern = re.compile("(" + "|".join(re.escape(k) for k in keywords_sorted) + ")", re.IGNORECASE)

        def highlight_keywords(text: str) -> str:
            """Escape HTML and wrap keyword matches with <span class='kw'>."""
            if text is None:
                text = ""
            s = str(text)
            # Escape HTML special chars first
            s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if not keyword_pattern:
                return s
            return keyword_pattern.sub(lambda m: f'<span class="kw">{m.group(0)}</span>', s)

        html_content += "<table>"
        html_content += "<thead><tr>"
        header_classes = {
            "Matched_Stock": "col-stock",
            "Source": "col-source",
            "Published": "col-published",
            "Description": "col-description",
            "Link_Display": "col-link",
        }
        for col in display_cols:
            header = "Link" if col == "Link_Display" else col
            cls = header_classes.get(col, "")
            html_content += f"<th class=\"{cls}\">{header}</th>"
        html_content += "</tr></thead>"
        html_content += "<tbody id=\"resultsBody\">"

        for _, row in df_display.iterrows():
            # Determine if any configured keyword appears in this row (for row tinting)
            row_text = f"{row.get('Description','')} {row.get('Title','')} {row.get('Link_Display','')} {row.get('Attachment','')} {row.get('XBRL_Link','')}"
            kw_row = bool(keyword_pattern.search(str(row_text))) if keyword_pattern else False
            neg_row = bool(row.get("Has_Negative"))
            if neg_row:
                html_content += "<tr class=\"neg-row\">"
            elif kw_row:
                html_content += "<tr class=\"kw-row\">"
            else:
                html_content += "<tr>"
            for col in display_cols:
                cell_value = str(row.get(col, "")) if pd.notna(row.get(col, "")) else ""
                if col == "Link_Display" and cell_value:
                    # Link column: keep href as raw URL, but highlight keywords in link text if present
                    link_text = highlight_keywords(cell_value)
                    html_content += f'<td class="{header_classes.get(col, "")}"><a href="{cell_value}" target="_blank">{link_text}</a></td>'
                elif col == "Description":
                    # Add tooltip with categorized keyword matches
                    kw_tooltip = " | ".join(
                        [s for s in [row.get("KW_Universal"), row.get("KW_Sector"), row.get("KW_Filters")] if s]
                    )
                    if kw_tooltip:
                        html_content += f"<td class=\"{header_classes.get(col, '')}\" title=\"{kw_tooltip}\">{highlight_keywords(cell_value)}</td>"
                    else:
                        html_content += f"<td class=\"{header_classes.get(col, '')}\">{highlight_keywords(cell_value)}</td>"
                else:
                    # Escape HTML special characters
                    cell_value = cell_value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    html_content += f"<td class=\"{header_classes.get(col, '')}\">{cell_value}</td>"
            html_content += "</tr>"

        html_content += "</tbody></table>"
        html_content += f"""
        <script>
            (function() {{
                const input = document.getElementById("stockSearchInput");
                const btn = document.getElementById("stockSearchBtn");
                const resetBtn = document.getElementById("stockResetBtn");
                const excelInput = document.getElementById("excelSearchInput");
                const excelBtn = document.getElementById("excelSearchBtn");
                const tbody = document.getElementById("resultsBody");
                const totalMatchesEl = document.getElementById("totalMatches");
                const stocksSearchedEl = document.getElementById("stocksSearched");

                const HIGHLIGHT = {json.dumps([k for k in HIGHLIGHT_KEYWORDS if str(k).strip()])};
                const NEGATIVE = {json.dumps([k for k in NEGATIVE_KEYWORDS if str(k).strip()])};

                function normalize(s) {{
                    return (s || "").toString().toLowerCase().replace(/[^\\w\\s]/g, " ").replace(/\\s+/g, " ").trim();
                }}

                function highlight(text) {{
                    if (!text) return "";
                    let safe = text.toString().replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
                    if (!HIGHLIGHT.length) return safe;
                    const pattern = new RegExp("(" + HIGHLIGHT.map(k => k.replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&")).join("|") + ")", "gi");
                    return safe.replace(pattern, "<span class=\\"kw\\">$1</span>");
                }}

                function isNegative(textNorm) {{
                    if (!NEGATIVE.length) return false;
                    const padded = " " + textNorm + " ";
                    return NEGATIVE.some(k => padded.includes(" " + normalize(k) + " "));
                }}

                function renderRows(rows, matchedStockLabel) {{
                    tbody.innerHTML = "";
                    rows.forEach(r => {{
                        const negRow = !!r.Has_Negative || isNegative(r.Row_Blob || "");
                        const tr = document.createElement("tr");
                        if (negRow) tr.className = "neg-row";
                        const tooltip = [r.KW_Universal, r.KW_Sector, r.KW_Filters].filter(Boolean).join(" | ");
                        tr.innerHTML = `
                            <td class="col-stock">${{matchedStockLabel || r.Matched_Stock || ""}}</td>
                            <td class="col-source">${{r.Source || ""}}</td>
                            <td class="col-published">${{r.Published || ""}}</td>
                            <td class="col-description" title="${{tooltip}}">${{highlight(r.Description || "")}}</td>
                            <td class="col-link">${{r.Link ? `<a href="${{r.Link}}" target="_blank">${{highlight(r.Link)}}</a>` : ""}}</td>
                        `;
                        tbody.appendChild(tr);
                    }});
                    totalMatchesEl.textContent = rows.length.toString();
                    stocksSearchedEl.textContent = matchedStockLabel || "";
                }}

                function applyFilter() {{
                    const q = (input.value || "").toString().trim().toUpperCase();
                    const rows = Array.from(tbody.querySelectorAll("tr"));
                    rows.forEach(row => {{
                        const stockCell = row.querySelector("td.col-stock");
                        const text = (stockCell ? stockCell.textContent : "").toUpperCase();
                        row.style.display = (!q || text.includes(q)) ? "" : "none";
                    }});
                }}

                function searchExcel() {{
                    const qRaw = (excelInput.value || "").trim();
                    if (!qRaw) return;
                    fetch(`http://localhost:5005/search?stock=${{encodeURIComponent(qRaw)}}`)
                        .then(r => r.json())
                        .then(data => {{
                            renderRows(data.results || [], data.stock || qRaw.toUpperCase());
                        }})
                        .catch(() => {{
                            alert("Search server not running. Start it with: py rss_feed_server.py");
                        }});
                }}

                btn.addEventListener("click", applyFilter);
                input.addEventListener("keydown", (e) => {{
                    if (e.key === "Enter") applyFilter();
                }});
                resetBtn.addEventListener("click", () => {{
                    input.value = "";
                    applyFilter();
                }});
                excelBtn.addEventListener("click", searchExcel);
                excelInput.addEventListener("keydown", (e) => {{
                    if (e.key === "Enter") searchExcel();
                }});
            }})();
        </script>
        """
        html_content += """
        <script>
            (function() {
                const input = document.getElementById("stockSearchInput");
                const btn = document.getElementById("stockSearchBtn");
                const resetBtn = document.getElementById("stockResetBtn");
                const rows = Array.from(document.querySelectorAll("table tr")).slice(1);

                function normalize(s) {
                    return (s || "").toString().trim().toUpperCase();
                }

                function applyFilter() {
                    const q = normalize(input.value);
                    rows.forEach(row => {
                        const stockCell = row.querySelector("td.col-stock");
                        const text = normalize(stockCell ? stockCell.textContent : "");
                        if (!q || text.includes(q)) {
                            row.style.display = "";
                        } else {
                            row.style.display = "none";
                        }
                    });
                }

                btn.addEventListener("click", applyFilter);
                input.addEventListener("keydown", (e) => {
                    if (e.key === "Enter") {
                        applyFilter();
                    }
                });
                resetBtn.addEventListener("click", () => {
                    input.value = "";
                    applyFilter();
                });
            })();
        </script>
        """
    
    html_content += """
    </body>
    </html>
    """
    
    # Format the HTML with actual values (using replace instead of format to avoid CSS curly brace issues)
    stocks_searched = ", ".join(stocks_list)
    total_matches = len(df)
    html_content = html_content.replace("{stocks_searched}", stocks_searched)
    html_content = html_content.replace("{total_matches}", str(total_matches))
    
    # Write to file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    return output_path

# =====================
# FETCH RSS FEEDS
# =====================
def fetch_nse_feeds():
    """Fetch NSE feeds with session handling"""
    rows = []
    feed_stats = []
    
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=HEADERS, timeout=15)
    
    for RSS_URL in RSS_URLS:
        try:
            response = session.get(RSS_URL, headers=HEADERS, timeout=30)
            response.raise_for_status()
            
            feed = feedparser.parse(response.content)
            
            # Extract XML filename from URL (e.g., "Online_announcements" from "Online_announcements.xml")
            xml_filename = os.path.basename(urlparse(RSS_URL).path)
            feed_name = xml_filename.replace(".xml", "") if xml_filename.endswith(".xml") else xml_filename
            
            entry_count = len(feed.entries)
            feed_stats.append({"Source": feed_name, "Entries": entry_count, "Status": "Success"})
            
            # Parse feed entries
            for entry in feed.entries:
                rows.append({
                    "Source": feed_name,
                    "Published": entry.get("published", ""),
                    "Title": entry.get("title", ""),
                    "Link": entry.get("link", ""),
                    "Description": entry.get("summary", ""),
                    "FetchedAt": datetime.now(timezone.utc).replace(tzinfo=None)
                })
        except Exception as e:
            xml_filename = os.path.basename(urlparse(RSS_URL).path)
            feed_name = xml_filename.replace(".xml", "") if xml_filename.endswith(".xml") else xml_filename
            feed_stats.append({"Source": feed_name, "Entries": 0, "Status": f"Error: {str(e)}"})
            print(f"Error fetching {feed_name}: {str(e)}")
    
    return rows, feed_stats

def fetch_external_feeds():
    """Fetch external feeds (BSE, Moneycontrol, etc.)"""
    rows = []
    feed_stats = []
    
    for source_name, feed_url in EXTERNAL_FEEDS.items():
        try:
            response = requests.get(feed_url, headers=EXTERNAL_HEADERS, timeout=30)
            response.raise_for_status()
            
            feed = feedparser.parse(response.content)
            
            entry_count = len(feed.entries)
            feed_stats.append({"Source": source_name, "Entries": entry_count, "Status": "Success"})
            
            # Parse feed entries
            for entry in feed.entries:
                rows.append({
                    "Source": source_name,
                    "Published": entry.get("published", ""),
                    "Title": entry.get("title", "").strip(),
                    "Link": entry.get("link", "").strip(),
                    "Description": entry.get("summary", "").strip(),
                    "FetchedAt": datetime.now(timezone.utc).replace(tzinfo=None)
                })
        except Exception as e:
            feed_stats.append({"Source": source_name, "Entries": 0, "Status": f"Error: {str(e)}"})
            print(f"Error fetching {source_name}: {str(e)}")
    
    return rows, feed_stats


def _build_nse_corporate_rows(data, source_name):
    rows = []
    for item in data:
        seq_id = item.get("seq_id")
        has_xbrl = bool(item.get("hasXbrl"))
        xbrl_link = f"https://www.nseindia.com/api/xbrl/{seq_id}" if seq_id and has_xbrl else ""

        rows.append({
            "Source": source_name,
            "Published": item.get("an_dt") or item.get("sort_date") or "",
            "Title": item.get("desc") or "",
            "Link": xbrl_link or item.get("attchmntFile", ""),
            "Description": item.get("attchmntText") or "",
            "Symbol": item.get("symbol") or "",
            "Company": item.get("sm_name") or "",
            "ISIN": item.get("sm_isin") or "",
            "Industry": item.get("smIndustry") or "",
            "Attachment": item.get("attchmntFile") or "",
            "XBRL_Link": xbrl_link,
            "FetchedAt": datetime.now(timezone.utc).replace(tzinfo=None),
        })
    return rows


def _fetch_nse_corporate_api(session, page_url, api_url, source_name, params=None):
    rows = []
    feed_stats = []

    try:
        response = session.get(
            api_url,
            headers={**NSE_API_HEADERS, "Referer": page_url or "https://www.nseindia.com/"},
            params=params,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()

        entry_count = len(data)
        feed_stats.append({"Source": source_name, "Entries": entry_count, "Status": "Success"})
        rows.extend(_build_nse_corporate_rows(data, source_name))
    except Exception as e:
        feed_stats.append({"Source": source_name, "Entries": 0, "Status": f"Error: {str(e)}"})
        print(f"Error fetching {source_name}: {str(e)}")

    return rows, feed_stats


def fetch_nse_corporate_filings(session, page_config):
    """
    Fetch NSE corporate filings announcements from API and extract XBRL links.
    """
    rows = []
    feed_stats = []

    page_url = page_config.get("page_url")
    api_url = page_config.get("api_url")
    source_name = "NSE_Corporate_Filings"

    # Prime cookies (NSE requires homepage hit)
    session.get("https://www.nseindia.com", headers=NSE_API_HEADERS, timeout=15)
    if page_url:
        session.get(page_url, headers={**NSE_API_HEADERS, "Referer": page_url}, timeout=15)

    # Base API (typically 20 records)
    base_rows, base_stats = _fetch_nse_corporate_api(
        session,
        page_url,
        api_url,
        source_name
    )
    rows.extend(base_rows)
    feed_stats.extend(base_stats)

    # Range API (e.g., last 7 days) for exhaustive list
    range_days = page_config.get("range_days")
    if range_days:
        to_date = datetime.now().strftime("%d-%m-%Y")
        from_date = (datetime.now() - timedelta(days=int(range_days))).strftime("%d-%m-%Y")
        range_source = f"{source_name}_All"
        range_rows, range_stats = _fetch_nse_corporate_api(
            session,
            page_url,
            api_url,
            range_source,
            params={"from_date": from_date, "to_date": to_date}
        )
        rows.extend(range_rows)
        feed_stats.extend(range_stats)

    return rows, feed_stats


def fetch_nse_pages():
    """
    Fetch NSE non-RSS pages (currently corporate filings announcements).
    """
    if not NSE_PAGES:
        return [], []

    rows = []
    feed_stats = []
    session = requests.Session()

    # Currently only one NSE page type; expand as needed
    if "corporate_filings_announcements" in NSE_PAGES:
        page_rows, page_stats = fetch_nse_corporate_filings(session, NSE_PAGES["corporate_filings_announcements"])
        rows.extend(page_rows)
        feed_stats.extend(page_stats)

    return rows, feed_stats


def run_full_fetch():
    # Fetch all feeds
    print("Fetching NSE feeds...")
    nse_rows, nse_stats = fetch_nse_feeds()

    print("Fetching external feeds...")
    external_rows, external_stats = fetch_external_feeds()

    print("Fetching NSE non-RSS pages...")
    nse_page_rows, nse_page_stats = fetch_nse_pages()

    # Combine all rows and stats
    rows = nse_rows + external_rows + nse_page_rows
    feed_stats = nse_stats + external_stats + nse_page_stats

    df_new = pd.DataFrame(rows)

    # Ensure proper column order: Source first, standard columns, FetchedAt last
    if not df_new.empty:
        standard_cols = ["Source", "Published", "Title", "Link", "Description"]
        if "FetchedAt" in df_new.columns:
            cols = standard_cols + ["FetchedAt"]
            other_cols = [col for col in df_new.columns if col not in cols]
            cols = cols + other_cols
        else:
            cols = ["Source"] + [col for col in df_new.columns if col != "Source"]
        
        # Reorder columns, keeping only existing ones
        cols = [col for col in cols if col in df_new.columns]
        df_new = df_new[cols]

    # =====================
    # LOAD EXISTING EXCEL (if exists)
    # =====================
    if os.path.exists(EXCEL_FILE):
        df_existing = pd.read_excel(EXCEL_FILE)
        # If existing file doesn't have Source column, add it with empty values
        if "Source" not in df_existing.columns:
            df_existing.insert(0, "Source", "")
        # If existing file doesn't have FetchedAt column, add it with empty values
        if "FetchedAt" not in df_existing.columns:
            df_existing["FetchedAt"] = None
        
        df_final = pd.concat([df_existing, df_new], ignore_index=True)
        df_final.drop_duplicates(subset=["Link"], inplace=True)
        
        # Ensure proper column order: Source first, standard columns, FetchedAt last
        standard_cols = ["Source", "Published", "Title", "Link", "Description"]
        if "FetchedAt" in df_final.columns:
            cols = standard_cols + ["FetchedAt"]
            other_cols = [col for col in df_final.columns if col not in cols]
            cols = cols + other_cols
        else:
            cols = ["Source"] + [col for col in df_final.columns if col != "Source"]
        
        # Reorder columns, keeping only existing ones
        cols = [col for col in cols if col in df_final.columns]
        df_final = df_final[cols]
    else:
        df_final = df_new

    # =====================
    # FILTER TO LAST 30 DAYS PER SOURCE (INCLUDING TODAY)
    # =====================
    if not df_final.empty:
        # Calculate cutoff date (29 days ago at start of day, so we get today + 29 previous days = 30 days total)
        today = datetime.now().date()
        cutoff_date = today - timedelta(days=29)  # 29 days ago, so today + 29 days = 30 days total
        
        # Extract dates for each row
        df_final['ParsedDate'] = df_final.apply(get_record_date, axis=1)
        
        # Filter records per source to keep only last 30 days (including today)
        filtered_rows = []
        records_removed = 0
        
        for source in df_final['Source'].unique():
            source_df = df_final[df_final['Source'] == source].copy()
            
            # Filter records with valid dates within last 30 days (including today)
            # Convert parsed dates to date objects for comparison (ignore time component)
            def is_within_30_days(parsed_date):
                if parsed_date is None:
                    return False
                # dateutil.parser.parse() returns datetime, convert to date for comparison
                try:
                    if isinstance(parsed_date, datetime):
                        record_date = parsed_date.date()
                    else:
                        record_date = parsed_date
                    return record_date >= cutoff_date
                except:
                    return False
            
            source_df['Keep'] = source_df['ParsedDate'].apply(is_within_30_days)
            
            # Keep records with valid dates in last 30 days, or records without dates (keep them as fallback)
            source_df_filtered = source_df[
                (source_df['Keep'] == True) | (source_df['ParsedDate'].isna())
            ].copy()
            
            records_removed += len(source_df) - len(source_df_filtered)
            filtered_rows.append(source_df_filtered)
        
        # Combine filtered dataframes
        df_final = pd.concat(filtered_rows, ignore_index=True)
        
        # Drop the temporary columns
        df_final = df_final.drop(columns=['ParsedDate', 'Keep'], errors='ignore')
        
        # Ensure Source column is first
        cols = ["Source"] + [col for col in df_final.columns if col != "Source"]
        df_final = df_final[cols]
        
        if records_removed > 0:
            print(f"\nFiltered out {records_removed} records older than 30 days")

    # =====================
    # PRINT SUMMARY BEFORE SAVING
    # =====================
    print(f"\n=== Feed Processing Summary ===")
    feeds_with_entries = []
    feeds_without_entries = []
    for stat in feed_stats:
        if stat['Entries'] > 0:
            feeds_with_entries.append(stat)
        else:
            feeds_without_entries.append(stat)

    print(f"\nFeeds with entries ({len(feeds_with_entries)}):")
    for stat in feeds_with_entries:
        print(f"  {stat['Source']}: {stat['Entries']} entries")

    if feeds_without_entries:
        print(f"\nFeeds with NO entries ({len(feeds_without_entries)}):")
        for stat in feeds_without_entries:
            print(f"  {stat['Source']}: {stat['Entries']} entries - {stat['Status']}")

    print(f"\nTotal feeds processed: {len(feed_stats)}")
    print(f"New items fetched: {len(df_new)} items")
    print(f"Total records after filtering (last 30 days per source): {len(df_final)}")

    return df_final


def run_test_mode():
    # Do not fetch anything; load existing Excel and proceed to search + HTML
    if not os.path.exists(EXCEL_FILE):
        print(f"Error: Excel file not found at {EXCEL_FILE}")
        return pd.DataFrame()
    df_final = pd.read_excel(EXCEL_FILE)
    if "Source" not in df_final.columns:
        df_final.insert(0, "Source", "")
    return df_final


def is_running_in_ci():
    """Check if code is running in CI/GitHub Actions environment"""
    # Check for GitHub Actions
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        return True
    # Check for generic CI environment variable
    if os.environ.get('CI') == 'true':
        return True
    # Check if stdin is not a TTY (non-interactive)
    if not sys.stdin.isatty():
        return True
    return False

def is_interactive():
    """Check if code is running in interactive environment (CMD/Cursor)"""
    return not is_running_in_ci()


def choose_run_mode():
    if not is_interactive():
        return "full"
    mode = input("\nRun mode (test/full)? [full]: ").strip().lower()
    return mode if mode in ["test", "full"] else "full"


def main():
    # Decide run mode
    run_mode = choose_run_mode()
    if run_mode == "test":
        print("\nTest mode: skipping fetch and using existing Excel.")
        df_final = run_test_mode()
    else:
        df_final = run_full_fetch()

    # =====================
    # USER PROMPT: STOCK LIST FILTERING
    # =====================
    # Only show prompt if running interactively (not in CI/GitHub Actions)
    if is_interactive():
        user_input = input("\nDo you want to filter by a list of stocks? (yes/no): ").strip().lower()
    else:
        # Running in CI/GitHub Actions - skip stock filtering
        print("\nRunning in CI environment - skipping stock list filtering.")
        user_input = 'no'

    if user_input in ['yes', 'y']:
        # Get stocks list file path (only in interactive mode)
        if is_interactive():
            stocks_file_path = input("Enter the path to the stocks list file (.txt, .csv, or .xlsx): ").strip()
        else:
            # In CI mode, skip stock filtering
            print("Stock filtering skipped in CI environment.")
            stocks_file_path = None
        
        if stocks_file_path:
            # Remove quotes if user pasted path with quotes
            stocks_file_path = stocks_file_path.strip('"').strip("'")
            
            if not os.path.exists(stocks_file_path):
                print(f"Error: File not found at {stocks_file_path}")
                print("Proceeding with saving Excel file only...")
            else:
                # Read stocks list
                print(f"\nReading stocks list from {stocks_file_path}...")
                stocks_list = read_stocks_list(stocks_file_path)
                
                if not stocks_list:
                    print("No stocks found in the file. Proceeding with saving Excel file only...")
                else:
                    print(f"Found {len(stocks_list)} stocks to search: {', '.join(stocks_list[:10])}{'...' if len(stocks_list) > 10 else ''}")
                    
                    # Load stock symbol to company name mapping
                    print("\nLoading stock symbol to company name mapping...")
                    symbol_to_company_map = load_stock_company_mapping()
                    
                    # Show which company names were found
                    if symbol_to_company_map:
                        found_companies = []
                        for symbol in stocks_list[:10]:  # Show first 10
                            if symbol in symbol_to_company_map:
                                found_companies.append(f"{symbol} -> {symbol_to_company_map[symbol]}")
                        if found_companies:
                            print(f"Company names found: {', '.join(found_companies)}{'...' if len(stocks_list) > 10 else ''}")
                    
                    # Search for stocks in the DataFrame using both symbol and company name
                    print("\nSearching for stocks in NSE RSS feed data (using symbols and company names with fuzzy matching)...")
                    matched_df = search_stocks_in_dataframe(df_final, stocks_list, symbol_to_company_map)
                    
                    if matched_df.empty:
                        print("No matches found for the specified stocks.")
                    else:
                        print(f"Found {len(matched_df)} matching records!")
                        
                        # Generate HTML page
                        html_output_path = os.path.join(DATA_DIR, "stock_matches.html")
                        print(f"\nGenerating HTML page at {html_output_path}...")
                        generate_html_page(matched_df, stocks_list, html_output_path, df_full=df_final)
                        
                        # Open in browser (only in interactive mode)
                        if is_interactive():
                            print("Opening HTML page in browser...")
                            webbrowser.open(f"file:///{html_output_path.replace(os.sep, '/')}")
                            print("HTML page opened in browser.")
                        else:
                            print(f"HTML page generated at {html_output_path} (browser not opened in CI environment)")

    # =====================
    # SAVE TO EXCEL (only in full mode)
    # =====================
    if run_mode == "full":
        try:
            df_final.to_excel(EXCEL_FILE, index=False)
            print(f"\nSuccessfully saved to {EXCEL_FILE}")
        except PermissionError:
            print(f"\nError: Cannot save to {EXCEL_FILE}")
            print("  Please close the Excel file and try again.")
        except Exception as e:
            print(f"\nError saving file: {str(e)}")


if __name__ == "__main__":
    main()

