"""
Proof of Concept: Gemini 3 API for PDF Summary
Tests the Gemini API to process PDF files (results/concall) and generate summaries.
Uses PDF preprocessing pipeline: extract → clean → chunk → summarize
"""
import os
import sys
import json
import time
import re
from pathlib import Path
from typing import Optional

# Import PDF processor
try:
    from pdf_processor import process_pdf, ProcessedPDF
    PDF_PROCESSOR_AVAILABLE = True
except ImportError:
    PDF_PROCESSOR_AVAILABLE = False
    print("Warning: pdf_processor module not found")

# Try to import google-genai (new SDK)
try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("Warning: google-genai not installed. Install with: pip install google-genai")

# Configuration
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"


def load_config():
    """Load configuration from config file."""
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def load_gemini_api_key():
    """Load Gemini API key from environment variable or config file."""
    # First try environment variable
    api_key = os.getenv(GEMINI_API_KEY_ENV)
    
    if api_key:
        return api_key
    
    # Try config file
    config = load_config()
    api_key = config.get("gemini_api_key")
    if api_key:
        return api_key
    
    return None


def load_gemini_prompts(stock_name=None):
    """Load Gemini prompts from config file.
    
    Args:
        stock_name: Optional stock name to include in prompt
    """
    config = load_config()
    prompts = config.get("gemini_prompts", {})
    
    # Stock name prefix for prompt
    stock_prefix = f"Company: {stock_name}\n\n" if stock_name else ""
    
    # Default prompts if not in config
    default_single = f"""{stock_prefix}Summarise the attached company disclosures ONLY from a stock catalyst perspective.

Focus on:
- New orders or revenue visibility
- Capacity expansion or capex
- Margin, cost, or profitability commentary
- Balance sheet changes
- Management guidance or outlook
- Strategic or structural developments

Ignore:
- Boilerplate
- Legal text
- Repetition
- Detailed financial tables

Return the output in JSON with keys:
- growth_orders
- margins_costs
- capex_capacity
- balance_sheet
- management_outlook
- strategic_events

If there are no meaningful catalysts, return an empty JSON object."""
    
    stock_prefix_chunk = f"Company: {stock_name}\n\n" if stock_name else ""
    default_chunk = f"""{stock_prefix_chunk}Summarise this section of the company disclosures ONLY from a stock catalyst perspective.

Focus on:
- New orders or revenue visibility
- Capacity expansion or capex
- Margin, cost, or profitability commentary
- Balance sheet changes
- Management guidance or outlook
- Strategic or structural developments

Ignore:
- Boilerplate
- Legal text
- Repetition
- Detailed financial tables

Return the output in JSON with keys:
- growth_orders
- margins_costs
- capex_capacity
- balance_sheet
- management_outlook
- strategic_events

If there are no meaningful catalysts in this section, return an empty JSON object."""
    
    default_combine = """You have been provided with JSON summaries from different sections of a company disclosure document. Each section summary contains catalyst information in JSON format with keys: growth_orders, margins_costs, capex_capacity, balance_sheet, management_outlook, strategic_events.

Please combine these section summaries into a single, comprehensive JSON object with the same keys.

Rules:
- Merge information from all sections
- Eliminate redundancy
- Keep only the most important and specific catalyst information
- If a key has no meaningful catalysts across all sections, use an empty array or null
- Maintain JSON structure with keys: growth_orders, margins_costs, capex_capacity, balance_sheet, management_outlook, strategic_events

Return ONLY the final combined JSON object, no additional text."""
    
    return {
        "single_request": prompts.get("single_request", default_single),
        "chunk_request": prompts.get("chunk_request", default_chunk),
        "combine_request": prompts.get("combine_request", default_combine)
    }


def setup_gemini_client(api_key=None):
    """Setup Gemini API client."""
    if not GEMINI_AVAILABLE:
        raise ImportError("google-genai library not installed. Install with: pip install google-genai")
    
    if not api_key:
        api_key = load_gemini_api_key()
    
    if not api_key:
        raise ValueError(
            f"Gemini API key not found. Please set {GEMINI_API_KEY_ENV} environment variable "
            f"or add 'gemini_api_key' to {CONFIG_FILE}"
        )
    
    # Create client with API key
    client = genai.Client(api_key=api_key)
    
    return client


# Rate limiting: Track last request time for spacing
_last_request_time = 0
MIN_REQUEST_INTERVAL = 6  # 6 seconds = 10 requests per minute (conservative for 10-15 RPM limit)


def extract_retry_delay_from_error(error: Exception) -> Optional[float]:
    """Extract retry delay from 429 error message if available."""
    error_str = str(error)
    # Look for "Please retry in X.XXs" pattern
    match = re.search(r'Please retry in ([\d.]+)s', error_str)
    if match:
        return float(match.group(1))
    return None


def enforce_rate_limit():
    """Enforce minimum interval between requests to stay under RPM limits."""
    global _last_request_time
    current_time = time.time()
    time_since_last = current_time - _last_request_time
    
    if time_since_last < MIN_REQUEST_INTERVAL:
        wait_time = MIN_REQUEST_INTERVAL - time_since_last
        if wait_time > 0.1:  # Only show message if waiting more than 0.1s
            print(f"  [RATE LIMIT] Waiting {wait_time:.1f}s to stay under 10 RPM...")
        time.sleep(wait_time)
    
    _last_request_time = time.time()


def call_gemini_with_retry(client, model_name: str, contents, max_retries: int = 5, base_delay: float = 2.0):
    """
    Call Gemini API with automatic retry and exponential backoff for rate limits.
    
    Args:
        client: Gemini client
        model_name: Model name
        contents: Content to send
        max_retries: Maximum number of retries
        base_delay: Base delay in seconds for exponential backoff
    
    Returns:
        Response from API
    """
    for attempt in range(max_retries + 1):
        try:
            # Enforce rate limit before making request
            enforce_rate_limit()
            
            response = client.models.generate_content(
                model=model_name,
                contents=contents
            )
            return response
        except Exception as e:
            error_str = str(e)
            is_rate_limit = '429' in error_str or 'RESOURCE_EXHAUSTED' in error_str or 'quota' in error_str.lower()
            
            if is_rate_limit and attempt < max_retries:
                # Try to extract retry delay from error
                retry_delay = extract_retry_delay_from_error(e)
                
                if retry_delay:
                    delay = retry_delay + 1.0  # Add 1 second buffer
                else:
                    # Exponential backoff: 2s, 4s, 8s, 16s, 32s
                    delay = base_delay * (2 ** attempt)
                
                print(f"  [WARNING] Rate limit hit (attempt {attempt + 1}/{max_retries + 1})")
                print(f"  Waiting {delay:.1f} seconds before retry...")
                time.sleep(delay)
                continue
            else:
                # Not a rate limit error, or max retries reached
                raise


def summarize_pdf_with_gemini(pdf_path, model_name="gemini-2.5-flash-lite", use_preprocessing=True, stock_name=None):
    """
    Summarize a PDF file using Gemini API with optional preprocessing.
    
    Args:
        pdf_path: Path to the PDF file
        model_name: Gemini model to use (default: gemini-2.5-flash-lite)
        use_preprocessing: Whether to use PDF preprocessing pipeline (extract, clean, chunk)
        stock_name: Optional stock name to include in prompt
    
    Returns:
        Summary text as string
    """
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    
    if not pdf_path.lower().endswith('.pdf'):
        raise ValueError(f"File is not a PDF: {pdf_path}")
    
    print(f"Setting up Gemini API client...")
    client = setup_gemini_client()
    
    print(f"Loading PDF: {pdf_path}")
    print(f"File size: {os.path.getsize(pdf_path) / 1024:.2f} KB")
    
    print(f"Using model: {model_name}")
    
    # Preprocess PDF if enabled
    if use_preprocessing and PDF_PROCESSOR_AVAILABLE:
        print(f"\n{'=' * 80}")
        print("PDF PREPROCESSING")
        print(f"{'=' * 80}")
        processed_pdf = process_pdf(pdf_path)
        
        print(f"\nPreprocessing Results:")
        print(f"  Extraction method: {processed_pdf.metadata['extraction_method']}")
        print(f"  Pages: {processed_pdf.metadata['page_count']}")
        print(f"  Characters: {processed_pdf.metadata['character_count']:,}")
        print(f"  Needs chunking: {processed_pdf.metadata['needs_chunking']}")
        print(f"  Chunks: {processed_pdf.metadata['chunk_count']}")
        
        # Process chunks
        if processed_pdf.metadata['needs_chunking']:
            return summarize_chunked_pdf(processed_pdf, client, model_name, stock_name=stock_name)
        else:
            # Single chunk - process directly
            return summarize_text_with_gemini(processed_pdf.cleaned_text, client, model_name, stock_name=stock_name)
    else:
        # Fallback: Direct PDF upload (original method)
        if not use_preprocessing:
            print(f"  Note: Preprocessing disabled, using direct PDF upload")
        else:
            print(f"  Warning: PDF processor not available, using direct PDF upload")
        
        return summarize_pdf_direct_upload(pdf_path, client, model_name, stock_name=stock_name)


def summarize_text_with_gemini(text: str, client, model_name: str, stock_name: str = None) -> str:
    """Summarize text directly using Gemini API with catalyst-focused prompt.
    
    Args:
        text: Extracted and cleaned text from PDF
        client: Gemini client
        model_name: Model name to use
        stock_name: Optional stock name to include in prompt
    """
    prompts = load_gemini_prompts(stock_name=stock_name)
    prompt = prompts["single_request"]

    print(f"\nGenerating catalyst summary from extracted text...")
    response = call_gemini_with_retry(client, model_name, [text, prompt])
    summary = response.text
    print(f"  [OK] Summary generated ({len(summary)} characters)")
    return summary


def summarize_chunked_pdf(processed_pdf: ProcessedPDF, client, model_name: str, stock_name: str = None) -> str:
    """
    Summarize a chunked PDF using 2-pass approach.
    Pass 1: Summarize each chunk
    Pass 2: Combine chunk summaries into final summary
    
    Args:
        processed_pdf: ProcessedPDF object with chunks
        client: Gemini client
        model_name: Model name to use
        stock_name: Optional stock name to include in prompt
    """
    print(f"\n{'=' * 80}")
    print("CHUNK PROCESSING (2-Pass Approach)")
    print(f"{'=' * 80}")
    
    chunk_summaries = []
    
    prompts = load_gemini_prompts(stock_name=stock_name)
    chunk_prompt = prompts["chunk_request"]
    
    # Pass 1: Summarize each chunk
    for i, chunk in enumerate(processed_pdf.chunks, 1):
        print(f"\nProcessing chunk {i}/{len(processed_pdf.chunks)} ({len(chunk):,} characters)...")

        try:
            response = call_gemini_with_retry(client, model_name, [chunk, chunk_prompt])
            chunk_summary = response.text
            chunk_summaries.append(chunk_summary)
            print(f"  [OK] Chunk {i} summarized ({len(chunk_summary)} characters)")
        except Exception as e:
            print(f"  [ERROR] Error summarizing chunk {i}: {e}")
            # Continue with other chunks
            continue
    
    if not chunk_summaries:
        raise Exception("Failed to summarize any chunks")
    
    # Pass 2: Combine chunk summaries into final summary
    print(f"\n{'=' * 80}")
    print("COMBINING SUMMARIES (Pass 2)")
    print(f"{'=' * 80}")
    
    combined_summaries = "\n\n".join([
        f"=== Section {i+1} Summary ===\n{summary}"
        for i, summary in enumerate(chunk_summaries)
    ])
    
    final_prompt = prompts["combine_request"]

    print(f"Generating final combined summary from {len(chunk_summaries)} section summaries...")
    response = call_gemini_with_retry(client, model_name, [combined_summaries, final_prompt])
    final_summary = response.text
    print(f"  [OK] Final summary generated ({len(final_summary)} characters)")
    
    return final_summary


def summarize_pdf_direct_upload(pdf_path: str, client, model_name: str, stock_name: str = None) -> str:
    """Original method: Upload PDF directly to Gemini (fallback)."""
    prompts = load_gemini_prompts(stock_name=stock_name)
    prompt = prompts["single_request"]
    
    print(f"Uploading PDF to Gemini...")
    try:
        # Upload file using new API - open file and upload with mime_type
        with open(pdf_path, 'rb') as f:
            uploaded_file = client.files.upload(
                file=f, 
                config={
                    'display_name': os.path.basename(pdf_path),
                    'mime_type': 'application/pdf'
                }
            )
        print(f"  [OK] File uploaded. File name: {uploaded_file.name}")

        print(f"Generating catalyst summary...")
        # Use file in contents - new API accepts file objects directly
        response = call_gemini_with_retry(client, model_name, [uploaded_file, prompt])
        
        # Delete uploaded file
        client.files.delete(name=uploaded_file.name)
        print(f"  [OK] File deleted from Gemini storage")
        
        summary = response.text
        print(f"  [OK] Summary generated ({len(summary)} characters)")
        
        return summary
        
    except Exception as e:
        print(f"Error during PDF processing: {e}")
        raise


def main():
    """Main function for POC testing."""
    print("=" * 80)
    print("Gemini 3 API - PDF Summary POC")
    print("=" * 80)
    
    # Check if API key is available
    api_key = load_gemini_api_key()
    if not api_key:
        print(f"\n[ERROR] Gemini API key not found!")
        print(f"\nTo set up:")
        print(f"1. Get API key from: https://aistudio.google.com/app/apikey")
        print(f"2. Set environment variable: {GEMINI_API_KEY_ENV}=your_api_key")
        print(f"   OR add to config.json: \"gemini_api_key\": \"your_api_key\"")
        return
    
    print(f"\n[OK] API key found")
    
    # Get PDF file path
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
    else:
        pdf_path = input("\nEnter path to PDF file (results/concall): ").strip().strip('"').strip("'")
    
    if not pdf_path:
        print("[ERROR] No PDF file provided")
        return
    
    # Expand user path if needed
    pdf_path = os.path.expanduser(pdf_path)
    
    # Try to summarize with preprocessing
    try:
        print(f"\n{'=' * 80}")
        print("PROCESSING WITH PREPROCESSING PIPELINE")
        print(f"{'=' * 80}")
        summary = summarize_pdf_with_gemini(pdf_path, use_preprocessing=True)
        
        print(f"\n{'=' * 80}")
        print("FINAL SUMMARY")
        print(f"{'=' * 80}")
        print(summary)
        print(f"\n{'=' * 80}")
        
        # Save summary to file
        output_file = os.path.join(os.path.dirname(pdf_path), f"{Path(pdf_path).stem}_summary.txt")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"PDF: {pdf_path}\n")
            f.write(f"Generated: {os.path.basename(__file__)}\n")
            f.write(f"{'=' * 80}\n\n")
            f.write(summary)
        
        print(f"\n[OK] Summary saved to: {output_file}")
        
    except Exception as e:
        print(f"\n[ERROR] Error: {e}")
        import traceback
        traceback.print_exc()
        
        # Try fallback: direct upload without preprocessing
        print(f"\n{'=' * 80}")
        print("Trying fallback: Direct PDF upload (no preprocessing)...")
        print(f"{'=' * 80}")
        try:
            client = setup_gemini_client()
            summary = summarize_pdf_direct_upload(pdf_path, client, "gemini-2.0-flash-exp")
            
            print(f"\n{'=' * 80}")
            print("SUMMARY (Fallback Method)")
            print(f"{'=' * 80}")
            print(summary)
            
            # Save summary
            output_file = os.path.join(os.path.dirname(pdf_path), f"{Path(pdf_path).stem}_summary.txt")
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(f"PDF: {pdf_path}\n")
                f.write(f"Generated: {os.path.basename(__file__)} (Fallback)\n")
                f.write(f"{'=' * 80}\n\n")
                f.write(summary)
            
            print(f"\n[OK] Summary saved to: {output_file}")
            
        except Exception as e2:
            print(f"\n[ERROR] Fallback method also failed: {e2}")


if __name__ == "__main__":
    main()
