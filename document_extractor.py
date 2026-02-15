"""
Unified document extraction: PDF, XML (MainI), CMS and HTML (via readability).
Extracts plain text from URLs for Gemini analysis.
"""
import os
import re
import xml.etree.ElementTree as ET
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests

# PDF
try:
    from pdf_processor import process_pdf, clean_text
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False
    clean_text = lambda x: (x or "").strip()

# Readability for CMS
try:
    from readability import Document
    from bs4 import BeautifulSoup
    READABILITY_AVAILABLE = True
except ImportError:
    READABILITY_AVAILABLE = False

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0"
NSE_REFERER = "https://www.nseindia.com/"


def get_document_type(url: str) -> str:
    """Classify URL as pdf, xml, or cms (includes .cms and .html pages)."""
    url_lower = url.lower().strip()
    if url_lower.endswith(".pdf"):
        return "pdf"
    if ".xml" in url_lower or "nseindia.com/api/xbrl/" in url_lower:
        return "xml"
    if ".cms" in url_lower or ".html" in url_lower or ".ece" in url_lower:
        return "cms"  # CMS, HTML and Hindu .ece article pages use readability extraction
    return "unknown"


def _fetch_xml_nse(url: str) -> str:
    """Fetch XML from NSE XBRL API (requires session priming)."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/xml,application/json,text/xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": NSE_REFERER,
    })
    session.get("https://www.nseindia.com", timeout=15)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


def _fetch_generic(url: str) -> str:
    """Fetch content with generic headers."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml,text/xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def _extract_maini_text(xml_content: str) -> str:
    """Parse XML and extract text from MainI elements (case-insensitive local name)."""
    texts = []
    try:
        root = ET.fromstring(xml_content)
        for elem in root.iter():
            local_name = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local_name.lower() == "maini":
                text = "".join(elem.itertext()).strip()
                if text:
                    texts.append(text)
        if texts:
            return "\n\n".join(texts)
        fallback = ET.tostring(root, encoding="unicode", method="text")
        if fallback:
            return re.sub(r"\s+", " ", fallback).strip()
    except ET.ParseError:
        pass
    return ""


def extract_text_from_url(
    url: str,
    temp_dir: Optional[str] = None,
) -> Tuple[Optional[str], str, Optional[str]]:
    """
    Extract plain text from a document URL.

    Returns:
        (text, label, error): text on success, None on failure; label for logging; error message if failed.
    """
    doc_type = get_document_type(url)
    label = f"{doc_type}:{urlparse(url).path or url[:50]}"

    if doc_type == "pdf":
        if not PDF_AVAILABLE:
            return None, label, "PDF processor not available"
        if not temp_dir:
            import tempfile
            temp_dir = tempfile.mkdtemp()
        base_name = os.path.basename(urlparse(url).path) or "document"
        if not base_name.lower().endswith(".pdf"):
            base_name += ".pdf"
        pdf_path = os.path.join(temp_dir, base_name)
        try:
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "application/pdf,application/octet-stream,*/*",
            }
            resp = requests.get(url, headers=headers, timeout=60, stream=True)
            resp.raise_for_status()
            with open(pdf_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            if os.path.getsize(pdf_path) == 0:
                return None, label, "Downloaded PDF is empty"
            processed = process_pdf(pdf_path)
            text = processed.cleaned_text or processed.raw_text or ""
        except Exception as e:
            return None, label, str(e)
        finally:
            try:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
            except Exception:
                pass
        return text if text.strip() else None, label, None

    if doc_type == "xml":
        try:
            if "nseindia.com/api/xbrl/" in url.lower():
                content = _fetch_xml_nse(url)
            else:
                content = _fetch_generic(url)
            text = _extract_maini_text(content)
            if not text.strip():
                fallback = re.sub(r"<[^>]+>", " ", content)
                text = re.sub(r"\s+", " ", fallback).strip()
            return text if text.strip() else None, label, None
        except Exception as e:
            return None, label, str(e)

    if doc_type == "cms":
        if not READABILITY_AVAILABLE:
            return None, label, "readability-lxml not installed"
        try:
            html = _fetch_generic(url)
            doc = Document(html)
            summary_html = doc.summary()
            soup = BeautifulSoup(summary_html, "lxml")
            text = soup.get_text(separator="\n", strip=True)
            if not text.strip():
                soup_fallback = BeautifulSoup(html, "lxml")
                text = soup_fallback.get_text(separator="\n", strip=True)
            return text if text.strip() else None, label, None
        except Exception as e:
            return None, label, str(e)

    return None, label, f"Unsupported document type: {doc_type}"
