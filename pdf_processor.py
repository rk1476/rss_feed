"""
PDF Text Extraction and Preprocessing Module
Extracts text from PDFs, cleans it, and chunks if needed before sending to Gemini API.
"""
import os
import re
from typing import List, Dict, Optional, Tuple

# Try to import PDF libraries
try:
    import pdfplumber
    PDFPLUMBER_AVAILABLE = True
except ImportError:
    PDFPLUMBER_AVAILABLE = False

try:
    import pypdf
    PYPDF_AVAILABLE = True
except ImportError:
    PYPDF_AVAILABLE = False

try:
    from pdfminer.high_level import extract_text as pdfminer_extract
    PDFMINER_AVAILABLE = True
except ImportError:
    PDFMINER_AVAILABLE = False


class ProcessedPDF:
    """Container for processed PDF data."""
    def __init__(self):
        self.raw_text = ""
        self.cleaned_text = ""
        self.chunks = []
        self.metadata = {
            "extraction_method": None,
            "page_count": 0,
            "character_count": 0,
            "needs_chunking": False,
            "chunk_count": 0
        }


def extract_text_pdfplumber(pdf_path: str) -> Tuple[str, Dict]:
    """
    Extract text from PDF using pdfplumber (best for tables and structured data).
    
    Returns:
        (extracted_text, metadata)
    """
    if not PDFPLUMBER_AVAILABLE:
        raise ImportError("pdfplumber not installed. Install with: pip install pdfplumber")
    
    text_parts = []
    tables_text = []
    page_count = 0
    
    with pdfplumber.open(pdf_path) as pdf:
        page_count = len(pdf.pages)
        
        for page_num, page in enumerate(pdf.pages, 1):
            # Extract text
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
            
            # Extract tables (important for results/concall PDFs)
            tables = page.extract_tables()
            if tables:
                for table_num, table in enumerate(tables, 1):
                    # Convert table to readable text format
                    table_text = format_table_as_text(table)
                    if table_text:
                        tables_text.append(f"\n[Table {table_num} on Page {page_num}]\n{table_text}\n")
    
    # Combine text and tables
    full_text = "\n\n".join(text_parts)
    if tables_text:
        full_text += "\n\n" + "=" * 80 + "\nTABLES\n" + "=" * 80 + "\n\n" + "\n\n".join(tables_text)
    
    metadata = {
        "method": "pdfplumber",
        "page_count": page_count,
        "table_count": len(tables_text)
    }
    
    return full_text, metadata


def extract_text_pypdf(pdf_path: str) -> Tuple[str, Dict]:
    """
    Extract text from PDF using pypdf (simple, fast fallback).
    
    Returns:
        (extracted_text, metadata)
    """
    if not PYPDF_AVAILABLE:
        raise ImportError("pypdf not installed. Install with: pip install pypdf")
    
    text_parts = []
    page_count = 0
    
    with open(pdf_path, 'rb') as file:
        pdf_reader = pypdf.PdfReader(file)
        page_count = len(pdf_reader.pages)
        
        for page in pdf_reader.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    
    full_text = "\n\n".join(text_parts)
    
    metadata = {
        "method": "pypdf",
        "page_count": page_count
    }
    
    return full_text, metadata


def extract_text_pdfminer(pdf_path: str) -> Tuple[str, Dict]:
    """
    Extract text from PDF using pdfminer (last resort for difficult PDFs).
    
    Returns:
        (extracted_text, metadata)
    """
    if not PDFMINER_AVAILABLE:
        raise ImportError("pdfminer.six not installed. Install with: pip install pdfminer.six")
    
    text = pdfminer_extract(pdf_path)
    
    # Estimate page count (rough: ~2000 chars per page)
    estimated_pages = max(1, len(text) // 2000)
    
    metadata = {
        "method": "pdfminer",
        "page_count": estimated_pages
    }
    
    return text, metadata


def format_table_as_text(table: List[List]) -> str:
    """
    Convert a table (list of lists) to readable text format.
    Preserves structure while making it readable for Gemini.
    """
    if not table or not table[0]:
        return ""
    
    # Find max width for each column
    col_widths = []
    for col_idx in range(len(table[0])):
        max_width = 0
        for row in table:
            if col_idx < len(row) and row[col_idx]:
                cell_text = str(row[col_idx]).strip()
                max_width = max(max_width, len(cell_text))
        col_widths.append(max(max_width, 10))  # Minimum width 10
    
    # Format table
    lines = []
    for row_idx, row in enumerate(table):
        if not row:
            continue
        
        # Format row
        formatted_cells = []
        for col_idx, cell in enumerate(row):
            if col_idx < len(col_widths):
                cell_text = str(cell).strip() if cell else ""
                formatted_cells.append(cell_text.ljust(col_widths[col_idx]))
        
        lines.append(" | ".join(formatted_cells))
        
        # Add separator after header row
        if row_idx == 0:
            separator = "-" * (sum(col_widths) + len(col_widths) * 3 - 3)
            lines.append(separator)
    
    return "\n".join(lines)


def clean_text(text: str) -> str:
    """
    Clean extracted text:
    - Fix encoding
    - Normalize whitespace
    - Remove headers/footers
    - Remove artifacts
    - Normalize text
    """
    if not text:
        return ""
    
    # 1. Encoding fixes - normalize to UTF-8
    try:
        text = text.encode('utf-8', errors='ignore').decode('utf-8')
    except Exception:
        pass
    
    # 2. Remove page numbers and common footer patterns
    # Pattern: "Page X of Y" or "Page X" or "- X -"
    text = re.sub(r'(?i)\bpage\s+\d+\s+(?:of\s+\d+)?\b', '', text)
    text = re.sub(r'-\s*\d+\s*-', '', text)  # "- 1 -" style page numbers
    text = re.sub(r'^\d+$', '', text, flags=re.MULTILINE)  # Standalone page numbers
    
    # 3. Normalize whitespace
    # Replace multiple spaces with single space
    text = re.sub(r' +', ' ', text)
    # Replace multiple newlines with double newline (paragraph break)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove trailing whitespace from lines
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
    
    # 4. Fix broken words (hyphenation at line breaks)
    # Pattern: word-\nword -> wordword
    text = re.sub(r'(\w+)-\s*\n\s*(\w+)', r'\1\2', text)
    
    # 5. Normalize line breaks (preserve paragraph breaks)
    # Ensure consistent line break style
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    # 6. Remove excessive blank lines
    text = re.sub(r'\n{4,}', '\n\n\n', text)
    
    # 7. Strip leading/trailing whitespace
    text = text.strip()
    
    return text


def detect_sections(text: str) -> List[Dict]:
    """
    Detect sections in PDF text by looking for section headers.
    Common patterns for results/concall PDFs:
    - All caps lines
    - Numbered sections (1., 2., etc.)
    - Bold-like patterns (repeated characters)
    - Common section headers (Revenue, Profit, Guidance, etc.)
    """
    sections = []
    lines = text.split('\n')
    
    # Common section keywords for financial documents
    section_keywords = [
        'revenue', 'profit', 'guidance', 'outlook', 'results', 'financial',
        'operations', 'management', 'discussion', 'analysis', 'highlights',
        'summary', 'overview', 'conclusion', 'appendix', 'annexure'
    ]
    
    current_section = {"title": "Introduction", "start": 0, "end": 0}
    section_start = 0
    
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped or len(line_stripped) < 3:
            continue
        
        # Check if line looks like a section header
        is_header = False
        
        # Pattern 1: All caps and short (likely header)
        if line_stripped.isupper() and len(line_stripped) < 100 and len(line_stripped.split()) < 10:
            is_header = True
        
        # Pattern 2: Starts with number (1., 2., etc.)
        if re.match(r'^\d+[\.\)]\s+[A-Z]', line_stripped):
            is_header = True
        
        # Pattern 3: Contains section keywords
        line_lower = line_stripped.lower()
        if any(keyword in line_lower for keyword in section_keywords):
            if len(line_stripped) < 150:  # Not too long
                is_header = True
        
        # Pattern 4: Standalone line with few words (3-8 words)
        word_count = len(line_stripped.split())
        if 3 <= word_count <= 8 and i > 0 and not lines[i-1].strip():
            # Check if next line is not empty (header usually followed by content)
            if i + 1 < len(lines) and lines[i+1].strip():
                is_header = True
        
        if is_header:
            # Save previous section
            if current_section["start"] < i:
                current_section["end"] = i
                sections.append(current_section.copy())
            
            # Start new section
            current_section = {
                "title": line_stripped,
                "start": i,
                "end": len(lines)  # Will be updated when next section found
            }
    
    # Add final section
    if current_section["start"] < len(lines):
        current_section["end"] = len(lines)
        sections.append(current_section)
    
    # Convert line indices to character positions
    char_pos = 0
    line_positions = [0]
    for line in lines:
        char_pos += len(line) + 1  # +1 for newline
        line_positions.append(char_pos)
    
    # Update sections with character positions
    for section in sections:
        section["char_start"] = line_positions[section["start"]]
        section["char_end"] = line_positions[min(section["end"], len(line_positions) - 1)]
    
    return sections


def chunk_text_semantic(text: str, max_chunk_size: int = 500000, overlap_chars: int = 50000) -> List[str]:
    """
    Chunk text semantically by sections, with overlap.
    
    Args:
        text: Text to chunk
        max_chunk_size: Maximum characters per chunk
        overlap_chars: Number of characters to overlap between chunks
    
    Returns:
        List of text chunks
    """
    # Detect sections
    sections = detect_sections(text)
    
    if not sections:
        # Fallback to page-based chunking if no sections detected
        return chunk_text_page_based(text, max_chunk_size, overlap_chars)
    
    chunks = []
    current_chunk = ""
    current_size = 0
    
    for i, section in enumerate(sections):
        section_text = text[section["char_start"]:section["char_end"]]
        section_size = len(section_text)
        
        # If section itself is too large, split it
        if section_size > max_chunk_size:
            # Save current chunk if any
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
                current_size = 0
            
            # Split large section into smaller chunks
            sub_chunks = chunk_text_fixed_size(section_text, max_chunk_size, overlap_chars)
            chunks.extend(sub_chunks)
            continue
        
        # Check if adding this section would exceed max size
        if current_size + section_size > max_chunk_size and current_chunk:
            # Save current chunk
            chunks.append(current_chunk.strip())
            
            # Start new chunk with overlap from previous
            if overlap_chars > 0 and len(chunks) > 0:
                # Get last part of previous chunk for overlap
                prev_chunk = chunks[-1]
                overlap_text = prev_chunk[-overlap_chars:] if len(prev_chunk) > overlap_chars else prev_chunk
                current_chunk = overlap_text + "\n\n" + section_text
                current_size = len(current_chunk)
            else:
                current_chunk = section_text
                current_size = section_size
        else:
            # Add section to current chunk
            if current_chunk:
                current_chunk += "\n\n" + section_text
            else:
                current_chunk = section_text
            current_size = len(current_chunk)
    
    # Add final chunk
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks


def chunk_text_page_based(text: str, max_chunk_size: int = 500000, overlap_chars: int = 50000) -> List[str]:
    """
    Chunk text by pages (fallback method).
    Assumes ~2000 characters per page.
    """
    # Estimate pages (rough: ~2000 chars per page)
    estimated_pages = max(1, len(text) // 2000)
    chars_per_page = len(text) // estimated_pages if estimated_pages > 0 else len(text)
    
    # Calculate pages per chunk
    pages_per_chunk = max(1, max_chunk_size // chars_per_page)
    
    chunks = []
    chunk_size = pages_per_chunk * chars_per_page
    
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end]
        
        # Add overlap from previous chunk
        if start > 0 and overlap_chars > 0:
            prev_chunk = chunks[-1] if chunks else ""
            overlap_text = prev_chunk[-overlap_chars:] if len(prev_chunk) > overlap_chars else prev_chunk
            chunk = overlap_text + "\n\n" + chunk
        
        chunks.append(chunk.strip())
        start = end - overlap_chars  # Overlap with next chunk
    
    return chunks


def chunk_text_fixed_size(text: str, max_chunk_size: int, overlap_chars: int) -> List[str]:
    """
    Chunk text by fixed size (for splitting large sections).
    """
    chunks = []
    start = 0
    
    while start < len(text):
        end = min(start + max_chunk_size, len(text))
        chunk = text[start:end]
        
        # Try to break at sentence boundary near the end
        if end < len(text):
            # Look for sentence ending within last 200 chars
            last_period = chunk.rfind('.', max(0, len(chunk) - 200))
            last_newline = chunk.rfind('\n', max(0, len(chunk) - 200))
            break_point = max(last_period, last_newline)
            
            if break_point > len(chunk) * 0.8:  # If break point is in last 20%
                chunk = chunk[:break_point + 1]
                end = start + break_point + 1
        
        chunks.append(chunk.strip())
        
        # Move start with overlap
        start = end - overlap_chars
    
    return chunks


def estimate_tokens(text: str) -> int:
    """
    Rough estimate of tokens (1 token ≈ 4 characters for English text).
    """
    return len(text) // 4


def needs_chunking(text: str, page_count: int = 0) -> Tuple[bool, str]:
    """
    Determine if text needs chunking.
    Checks: > 50 pages OR > 500K characters (whichever occurs first).
    
    Returns:
        (needs_chunking, reason)
    """
    char_count = len(text)
    token_estimate = estimate_tokens(text)
    
    # Check page count (if available)
    if page_count > 50:
        return True, f"Page count ({page_count}) exceeds 50 pages"
    
    # Check character count
    if char_count > 500000:
        return True, f"Character count ({char_count:,}) exceeds 500,000"
    
    # Check token estimate (safety check)
    if token_estimate > 125000:  # 500K chars / 4 ≈ 125K tokens
        return True, f"Estimated tokens ({token_estimate:,}) exceeds 125,000"
    
    return False, "Within limits"


def process_pdf(pdf_path: str) -> ProcessedPDF:
    """
    Main function to process PDF: extract, clean, and chunk if needed.
    
    Uses fallback chain: pdfplumber → pypdf → pdfminer
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    result = ProcessedPDF()
    
    # Try extraction methods in order
    extraction_methods = [
        ("pdfplumber", extract_text_pdfplumber, PDFPLUMBER_AVAILABLE),
        ("pypdf", extract_text_pypdf, PYPDF_AVAILABLE),
        ("pdfminer", extract_text_pdfminer, PDFMINER_AVAILABLE)
    ]
    
    extracted_text = None
    metadata = {}
    
    for method_name, extract_func, is_available in extraction_methods:
        if not is_available:
            continue
        
        try:
            print(f"  Trying {method_name}...")
            extracted_text, metadata = extract_func(pdf_path)
            result.metadata["extraction_method"] = method_name
            result.metadata["page_count"] = metadata.get("page_count", 0)
            print(f"  [OK] Extracted using {method_name}")
            break
        except Exception as e:
            print(f"  [ERROR] {method_name} failed: {str(e)[:50]}")
            continue
    
    if not extracted_text:
        raise Exception("All PDF extraction methods failed")
    
    result.raw_text = extracted_text
    result.metadata["character_count"] = len(extracted_text)
    
    # Clean text
    print(f"  Cleaning text...")
    result.cleaned_text = clean_text(extracted_text)
    result.metadata["character_count"] = len(result.cleaned_text)
    
    # Check if chunking needed
    needs_chunk, reason = needs_chunking(
        result.cleaned_text, 
        result.metadata["page_count"]
    )
    result.metadata["needs_chunking"] = needs_chunk
    
    if needs_chunk:
        print(f"  Chunking needed: {reason}")
        # Calculate overlap (10% of max chunk size, minimum 100 words ≈ 500 chars)
        max_chunk_size = 500000
        overlap_chars = max(50000, max_chunk_size // 10)  # 10% overlap, min 50K chars
        
        # Use semantic chunking (best for results/concall PDFs)
        result.chunks = chunk_text_semantic(result.cleaned_text, max_chunk_size, overlap_chars)
        result.metadata["chunk_count"] = len(result.chunks)
        print(f"  [OK] Split into {len(result.chunks)} chunks")
    else:
        print(f"  No chunking needed")
        result.chunks = [result.cleaned_text]
        result.metadata["chunk_count"] = 1
    
    return result
