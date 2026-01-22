import json
import os
import shutil
import tempfile
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import pandas as pd

from rss_feed import (
    EXCEL_FILE,
    load_stock_company_mapping,
    search_stocks_in_dataframe,
    select_link_display,
    normalize_text,
    DATA_DIR,
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
    
    def _send_cors_headers(self):
        """Send CORS headers for preflight requests."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
    
    def do_OPTIONS(self):
        """Handle CORS preflight requests."""
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

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
            
            # Helper function to convert NaN/None to empty string
            def safe_str(value):
                if pd.isna(value) or value is None:
                    return ""
                return str(value)
            
            results.append({
                "Matched_Stock": safe_str(row.get("Matched_Stock", "")),
                "Source": safe_str(row.get("Source", "")),
                "Published": safe_str(row.get("Published", "")),
                "Description": safe_str(row.get("Description", "")),
                "Link": safe_str(link_display) if link_display else "",
                "KW_Universal": safe_str(row.get("KW_Universal", "")),
                "KW_Sector": safe_str(row.get("KW_Sector", "")),
                "KW_Filters": safe_str(row.get("KW_Filters", "")),
                "Has_Negative": bool(row.get("Has_Negative", False)),
                "Row_Blob": normalize_text(row_text),
            })

        return self._send_json({"stock": stock.upper(), "results": results})
    
    def do_POST(self):
        """Handle POST requests for PDF processing."""
        parsed = urlparse(self.path)
        
        if parsed.path == "/process_pdfs":
            return self._handle_process_pdfs()
        else:
            return self._send_json({"error": "Not found"}, status=404)
    
    def _handle_process_pdfs(self):
        """Handle PDF processing request."""
        try:
            # Read request body
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length == 0:
                return self._send_json({"error": "Empty request body"}, status=400)
            
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))
            
            stock = data.get("stock", "").strip()
            pdf_urls = data.get("pdf_urls", [])
            
            if not stock:
                return self._send_json({"error": "Missing stock parameter"}, status=400)
            
            if not pdf_urls or len(pdf_urls) == 0:
                return self._send_json({"error": "No PDF URLs provided"}, status=400)
            
            # For now, only process first PDF
            pdf_url = pdf_urls[0]
            
            # Download PDF
            temp_dir = os.path.join(DATA_DIR, "temp_pdfs")
            os.makedirs(temp_dir, exist_ok=True)
            
            pdf_filename = os.path.basename(urlparse(pdf_url).path) or "document.pdf"
            if not pdf_filename.lower().endswith(".pdf"):
                pdf_filename += ".pdf"
            
            pdf_path = os.path.join(temp_dir, pdf_filename)
            
            try:
                # Download PDF with increased timeout and retry logic
                max_retries = 3
                timeout = 60  # Increased to 60 seconds
                
                for attempt in range(max_retries):
                    try:
                        # Add headers to mimic browser request
                        headers = {
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                            'Accept': 'application/pdf,application/octet-stream,*/*',
                            'Accept-Language': 'en-US,en;q=0.9',
                            'Connection': 'keep-alive',
                        }
                        
                        response = requests.get(pdf_url, timeout=timeout, stream=True, headers=headers)
                        response.raise_for_status()
                        
                        # Download with progress tracking
                        total_size = 0
                        with open(pdf_path, "wb") as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                if chunk:
                                    f.write(chunk)
                                    total_size += len(chunk)
                        
                        # Verify file was downloaded (not empty)
                        if os.path.getsize(pdf_path) == 0:
                            raise Exception("Downloaded file is empty")
                        
                        break  # Success, exit retry loop
                        
                    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                        if attempt < max_retries - 1:
                            # Wait before retry (exponential backoff)
                            import time
                            wait_time = (attempt + 1) * 5  # 5s, 10s, 15s
                            time.sleep(wait_time)
                            continue
                        else:
                            raise Exception(f"Failed to download after {max_retries} attempts: {str(e)}")
                    except Exception as e:
                        # For other errors, don't retry
                        raise
                
                # Process PDF through pdf_processor and gemini_api
                from pdf_processor import process_pdf
                from gemini_api import summarize_pdf_with_gemini
                
                # Process PDF
                result = summarize_pdf_with_gemini(
                    pdf_path,
                    model_name="gemini-2.5-flash-lite",
                    use_preprocessing=True,
                    stock_name=stock
                )
                
                # Cleanup: delete downloaded PDF
                try:
                    os.remove(pdf_path)
                except Exception:
                    pass
                
                return self._send_json({
                    "stock": stock,
                    "result": result
                })
                
            except requests.RequestException as e:
                # Cleanup on error
                try:
                    if os.path.exists(pdf_path):
                        os.remove(pdf_path)
                except Exception:
                    pass
                return self._send_json({"error": f"Failed to download PDF: {str(e)}"}, status=500)
            
            except Exception as e:
                # Cleanup on error
                try:
                    if os.path.exists(pdf_path):
                        os.remove(pdf_path)
                except Exception:
                    pass
                return self._send_json({"error": f"Failed to process PDF: {str(e)}"}, status=500)
        
        except json.JSONDecodeError:
            return self._send_json({"error": "Invalid JSON in request body"}, status=400)
        except Exception as e:
            return self._send_json({"error": f"Server error: {str(e)}"}, status=500)

    def log_message(self, format, *args):
        # Quiet logging
        return


def run_server(host="127.0.0.1", port=5005):
    httpd = HTTPServer((host, port), SearchHandler)
    print(f"Search server running on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    run_server()
