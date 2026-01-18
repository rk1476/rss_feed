import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import pandas as pd

from rss_feed import (
    EXCEL_FILE,
    load_stock_company_mapping,
    search_stocks_in_dataframe,
    select_link_display,
    normalize_text,
)


class SearchCache:
    def __init__(self):
        self.df = None
        self.mtime = None
        self.symbol_map = None

    def load(self):
        if not os.path.exists(EXCEL_FILE):
            raise FileNotFoundError(f"Excel file not found: {EXCEL_FILE}")
        mtime = os.path.getmtime(EXCEL_FILE)
        if self.df is None or self.mtime != mtime:
            self.df = pd.read_excel(EXCEL_FILE)
            if "Source" not in self.df.columns:
                self.df.insert(0, "Source", "")
            self.symbol_map = load_stock_company_mapping()
            self.mtime = mtime


cache = SearchCache()


class SearchHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/search":
            return self._send_json({"error": "Not found"}, status=404)

        params = parse_qs(parsed.query or "")
        stock = (params.get("stock") or [""])[0].strip()
        if not stock:
            return self._send_json({"error": "Missing stock parameter"}, status=400)

        try:
            cache.load()
            results_df = search_stocks_in_dataframe(cache.df, [stock], cache.symbol_map)
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

        results = []
        for _, row in results_df.iterrows():
            link_display = select_link_display(row)
            row_text = f"{row.get('Title','')} {row.get('Description','')} {link_display} {row.get('Attachment','')} {row.get('XBRL_Link','')}"
            results.append({
                "Matched_Stock": row.get("Matched_Stock", ""),
                "Source": row.get("Source", ""),
                "Published": row.get("Published", ""),
                "Description": row.get("Description", ""),
                "Link": link_display or "",
                "KW_Universal": row.get("KW_Universal", ""),
                "KW_Sector": row.get("KW_Sector", ""),
                "KW_Filters": row.get("KW_Filters", ""),
                "Has_Negative": bool(row.get("Has_Negative", False)),
                "Row_Blob": normalize_text(row_text),
            })

        return self._send_json({"stock": stock.upper(), "results": results})

    def log_message(self, format, *args):
        # Quiet logging
        return


def run_server(host="127.0.0.1", port=5005):
    httpd = HTTPServer((host, port), SearchHandler)
    print(f"Search server running on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    run_server()
