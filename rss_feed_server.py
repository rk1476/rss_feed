import json
import os
import shutil
import tempfile
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import pandas as pd

from rss_feed import (
    EXCEL_FILE,
    load_stock_company_mapping,
    search_stocks_in_dataframe,
    select_link_display,
    normalize_text,
    read_stocks_list,
    DATA_DIR,
    HIGHLIGHT_KEYWORDS,
    NEGATIVE_KEYWORDS,
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
    def _safe_wfile_write(self, data):
        """Write to client; suppress errors when client disconnected (refresh/navigate/close)."""
        try:
            self.wfile.write(data)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError) as e:
            print(f"[Server] Client disconnected: {type(e).__name__}")

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self._safe_wfile_write(body)
    
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
        if parsed.path == "/all":
            return self._handle_all()
        if parsed.path == "/keywords":
            return self._handle_keywords()
        if parsed.path in ("/", "/page", "/stock_matches.html"):
            return self._serve_page()
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

    def _handle_all(self):
        """Return all match rows. Prefer saved JSON from last CLI run (stock list); else from Excel."""
        data_file = os.path.join(DATA_DIR, "stock_matches_data.json")
        if os.path.isfile(data_file):
            try:
                with open(data_file, "r", encoding="utf-8") as f:
                    results = json.load(f)
                if isinstance(results, list):
                    return self._send_json({"stock": "ALL", "results": results})
            except Exception:
                pass
        try:
            cache.load()
            df = cache.df
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)

        def safe_str(value):
            if pd.isna(value) or value is None:
                return ""
            return str(value)

        results = []
        for _, row in df.iterrows():
            matched_stock = safe_str(row.get("Matched_Stock", "")).strip()
            if not matched_stock:
                continue
            link_display = select_link_display(row)
            row_text = f"{row.get('Title','')} {row.get('Description','')} {link_display} {row.get('Attachment','')} {row.get('XBRL_Link','')}"
            results.append({
                "Matched_Stock": matched_stock,
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
        return self._send_json({"stock": "ALL", "results": results})

    def _handle_keywords(self):
        """Return highlight and negative keyword lists from config (same as UI highlighting)."""
        highlight = [k for k in (HIGHLIGHT_KEYWORDS or []) if str(k).strip()]
        negative = [k for k in (NEGATIVE_KEYWORDS or []) if str(k).strip()]
        return self._send_json({"highlight": highlight, "negative": negative})

    def _serve_page(self):
        """Serve stock_matches.html so the app is same-origin and fetch(/all) works."""
        path = os.path.join(DATA_DIR, "stock_matches.html")
        if not os.path.isfile(path):
            return self._send_json({"error": "stock_matches.html not found in data dir"}, status=404)
        try:
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html.encode("utf-8"))))
        self.end_headers()
        self._safe_wfile_write(html.encode("utf-8"))

    def _parse_multipart_file(self, body, content_type):
        """Parse multipart/form-data body; return (file_bytes, filename) or (None, None)."""
        import re
        # Get boundary from Content-Type: multipart/form-data; boundary=----...
        m = re.search(r'boundary=([^;\s]+)', content_type, re.IGNORECASE)
        if not m:
            return None, None
        boundary = m.group(1).strip('"').encode("utf-8")
        parts = body.split(b"--" + boundary)
        for part in parts:
            if b'name="file"' not in part and b"name='file'" not in part:
                continue
            # Headers end at first \r\n\r\n or \n\n
            if b"\r\n\r\n" in part:
                headers, content = part.split(b"\r\n\r\n", 1)
            elif b"\n\n" in part:
                headers, content = part.split(b"\n\n", 1)
            else:
                continue
            content = content.rstrip(b"\r\n")
            if content.endswith(b"--"):
                content = content[:-2].rstrip(b"\r\n")
            # Parse filename from Content-Disposition
            filename = "upload.txt"
            for line in headers.split(b"\r\n"):
                if line.lower().startswith(b"content-disposition:"):
                    fm = re.search(r'filename[*]?=(?:"([^"]*)"|\'([^\']*)\'|([^\s;]+))', line.decode("latin-1"), re.IGNORECASE)
                    if fm:
                        filename = (fm.group(1) or fm.group(2) or fm.group(3) or "").strip()
                    break
            return content, filename
        return None, None

    def _handle_search_with_file(self):
        """Accept uploaded stock list file (.txt, .csv, .xlsx), search Excel, return matching rows."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            return self._send_json({"error": "No file uploaded"}, status=400)
        body = self.rfile.read(content_length)
        content_type = self.headers.get("Content-Type", "")
        file_bytes, filename = self._parse_multipart_file(body, content_type)
        if file_bytes is None:
            return self._send_json({"error": "No file in request"}, status=400)
        filename = filename or "upload.txt"
        ext = os.path.splitext(filename)[1].lower()
        if ext not in (".txt", ".csv", ".xlsx", ".xls"):
            return self._send_json({"error": "Use .txt, .csv, or .xlsx"}, status=400)
        suffix = ext if ext in (".csv", ".xlsx", ".xls") else ".txt"
        fd, path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(file_bytes)
            stocks_list = read_stocks_list(path)
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass
        if not stocks_list:
            return self._send_json({"error": "No stocks found in file", "stock": "", "results": []}, status=400)
        try:
            cache.load()
            symbol_map = load_stock_company_mapping()
            matched_df = search_stocks_in_dataframe(cache.df, stocks_list, symbol_map)
        except Exception as e:
            return self._send_json({"error": str(e)}, status=500)
        def safe_str(v):
            if pd.isna(v) or v is None:
                return ""
            return str(v)
        # Which uploaded stocks had at least one match (Matched_Stock can be "A" or "A, B")
        stocks_found_set = set()
        for _, row in matched_df.iterrows():
            ms = safe_str(row.get("Matched_Stock", "")).strip()
            if ms:
                for s in (x.strip() for x in ms.split(",") if x.strip()):
                    stocks_found_set.add(s.upper())
        stocks_found = sorted(stocks_found_set)
        stocks_not_found = sorted(set(s.upper() for s in stocks_list) - stocks_found_set)

        results = []
        for _, row in matched_df.iterrows():
            link_display = select_link_display(row)
            row_text = f"{row.get('Title','')} {row.get('Description','')} {link_display} {row.get('Attachment','')} {row.get('XBRL_Link','')}"
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
        return self._send_json({
            "stock": "UPLOADED",
            "results": results,
            "stocks_found": stocks_found,
            "stocks_not_found": stocks_not_found,
        })

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/process_pdfs":
            return self._handle_process_pdfs()
        if parsed.path == "/search_with_file":
            return self._handle_search_with_file()
        return self._send_json({"error": "Not found"}, status=404)
    
    def _download_pdf(self, pdf_url, pdf_path, max_retries=3, timeout=60):
        """Download a single PDF from URL to pdf_path. Raises on failure."""
        import time as time_mod
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'application/pdf,application/octet-stream,*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        }
        for attempt in range(max_retries):
            try:
                response = requests.get(pdf_url, timeout=timeout, stream=True, headers=headers)
                response.raise_for_status()
                with open(pdf_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                if os.path.getsize(pdf_path) == 0:
                    raise Exception("Downloaded file is empty")
                return
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 5
                    time_mod.sleep(wait_time)
                    continue
                raise Exception(f"Failed to download after {max_retries} attempts: {str(e)}")

    def _handle_process_pdfs(self):
        """Handle PDF processing request. Processes all pdf_urls; skips failed and combines rest."""
        try:
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

            from gemini_api import summarize_multiple_pdfs_with_gemini

            temp_dir = os.path.join(DATA_DIR, "temp_pdfs")
            # Clear previous stock's PDFs before fetching new ones
            if os.path.isdir(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass
            os.makedirs(temp_dir, exist_ok=True)

            total = len(pdf_urls)
            download_tasks = []
            for idx, pdf_url in enumerate(pdf_urls):
                base_name = os.path.basename(urlparse(pdf_url).path) or "document"
                if not base_name.lower().endswith(".pdf"):
                    base_name += ".pdf"
                pdf_filename = f"{idx}_{base_name}"
                pdf_path = os.path.join(temp_dir, pdf_filename)
                download_tasks.append((pdf_url, pdf_path))

            pdf_paths = []
            max_workers = min(6, max(1, len(download_tasks)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_path = {executor.submit(self._download_pdf, url, path): path for url, path in download_tasks}
                for future in as_completed(future_to_path):
                    path = future_to_path[future]
                    try:
                        future.result()
                        pdf_paths.append(path)
                    except Exception:
                        try:
                            if os.path.exists(path):
                                os.remove(path)
                        except Exception:
                            pass
            def _path_index(p):
                base = os.path.basename(p)
                prefix = base.split("_")[0] if "_" in base else base
                return int(prefix) if prefix.isdigit() else 0
            pdf_paths.sort(key=_path_index)

            try:
                if not pdf_paths:
                    return self._send_json({
                        "error": f"All {total} PDF(s) failed to download.",
                        "stock": stock,
                    }, status=500)
                result = summarize_multiple_pdfs_with_gemini(
                    pdf_paths,
                    model_name="gemini-2.5-flash-lite",
                    use_preprocessing=True,
                    stock_name=stock,
                )
                return self._send_json({
                    "stock": stock,
                    "result": result,
                    "processed_count": len(pdf_paths),
                    "total_count": total,
                })
            finally:
                for p in pdf_paths:
                    try:
                        if os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass

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
