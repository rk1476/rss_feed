"""
Script to show the processed data at each step of PDF preprocessing.
Shows what gets extracted, cleaned, and fed to Gemini API.
"""
import os
import sys
from pathlib import Path

# Import PDF processor
try:
    from pdf_processor import process_pdf, ProcessedPDF
    PDF_PROCESSOR_AVAILABLE = True
except ImportError:
    PDF_PROCESSOR_AVAILABLE = False
    print("Error: pdf_processor module not found")
    sys.exit(1)


def show_processed_data(pdf_path: str, output_dir: str = None):
    """
    Process PDF and show data at each step.
    
    Args:
        pdf_path: Path to PDF file
        output_dir: Directory to save output files (default: same as PDF)
    """
    if not os.path.exists(pdf_path):
        print(f"Error: PDF file not found: {pdf_path}")
        return
    
    if output_dir is None:
        output_dir = os.path.dirname(pdf_path)
    
    os.makedirs(output_dir, exist_ok=True)
    
    pdf_name = Path(pdf_path).stem
    
    print("=" * 80)
    print("PDF PROCESSING - DATA INSPECTION")
    print("=" * 80)
    print(f"\nPDF File: {pdf_path}")
    print(f"File Size: {os.path.getsize(pdf_path) / 1024:.2f} KB")
    print(f"Output Directory: {output_dir}")
    
    # Step 1: Process PDF
    print("\n" + "=" * 80)
    print("STEP 1: Processing PDF (Extract + Clean + Chunk)")
    print("=" * 80)
    
    try:
        processed_pdf = process_pdf(pdf_path)
    except Exception as e:
        print(f"Error processing PDF: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Show metadata
    print("\nProcessing Results:")
    print(f"  Extraction method: {processed_pdf.metadata['extraction_method']}")
    print(f"  Pages: {processed_pdf.metadata['page_count']}")
    print(f"  Characters (raw): {processed_pdf.metadata.get('character_count', len(processed_pdf.raw_text)):,}")
    print(f"  Characters (cleaned): {len(processed_pdf.cleaned_text):,}")
    print(f"  Needs chunking: {processed_pdf.metadata['needs_chunking']}")
    print(f"  Chunks: {processed_pdf.metadata['chunk_count']}")
    
    # Step 2: Show raw extracted text (first 2000 chars)
    print("\n" + "=" * 80)
    print("STEP 2: Raw Extracted Text (First 2000 characters)")
    print("=" * 80)
    raw_preview = processed_pdf.raw_text[:2000]
    print(raw_preview)
    if len(processed_pdf.raw_text) > 2000:
        print(f"\n... ({len(processed_pdf.raw_text) - 2000:,} more characters)")
    
    # Save raw text
    raw_file = os.path.join(output_dir, f"{pdf_name}_01_raw_extracted.txt")
    with open(raw_file, 'w', encoding='utf-8') as f:
        f.write(processed_pdf.raw_text)
    print(f"\n[SAVED] Full raw text: {raw_file}")
    
    # Step 3: Show cleaned text (first 2000 chars)
    print("\n" + "=" * 80)
    print("STEP 3: Cleaned Text (First 2000 characters)")
    print("=" * 80)
    cleaned_preview = processed_pdf.cleaned_text[:2000]
    print(cleaned_preview)
    if len(processed_pdf.cleaned_text) > 2000:
        print(f"\n... ({len(processed_pdf.cleaned_text) - 2000:,} more characters)")
    
    # Save cleaned text
    cleaned_file = os.path.join(output_dir, f"{pdf_name}_02_cleaned.txt")
    with open(cleaned_file, 'w', encoding='utf-8') as f:
        f.write(processed_pdf.cleaned_text)
    print(f"\n[SAVED] Full cleaned text: {cleaned_file}")
    
    # Step 4: Show what will be sent to Gemini
    print("\n" + "=" * 80)
    print("STEP 4: Data to be sent to Gemini API")
    print("=" * 80)
    
    if processed_pdf.metadata['needs_chunking']:
        print(f"\nDocument will be processed in {len(processed_pdf.chunks)} chunks:")
        for i, chunk in enumerate(processed_pdf.chunks, 1):
            print(f"\n--- Chunk {i}/{len(processed_pdf.chunks)} ---")
            print(f"Size: {len(chunk):,} characters")
            print(f"Preview (first 500 chars):")
            print(chunk[:500])
            if len(chunk) > 500:
                print(f"... ({len(chunk) - 500:,} more characters)")
            
            # Save each chunk
            chunk_file = os.path.join(output_dir, f"{pdf_name}_03_chunk_{i:02d}.txt")
            with open(chunk_file, 'w', encoding='utf-8') as f:
                f.write(chunk)
            print(f"[SAVED] Chunk {i}: {chunk_file}")
    else:
        print("\nDocument will be processed as a single request:")
        print(f"Size: {len(processed_pdf.cleaned_text):,} characters")
        print(f"Preview (first 1000 chars):")
        print(processed_pdf.cleaned_text[:1000])
        if len(processed_pdf.cleaned_text) > 1000:
            print(f"\n... ({len(processed_pdf.cleaned_text) - 1000:,} more characters)")
        
        # Save single chunk
        single_file = os.path.join(output_dir, f"{pdf_name}_03_single_chunk.txt")
        with open(single_file, 'w', encoding='utf-8') as f:
            f.write(processed_pdf.cleaned_text)
        print(f"\n[SAVED] Single chunk: {single_file}")
    
    # Step 5: Show prompt that will be used
    print("\n" + "=" * 80)
    print("STEP 5: Prompt that will be sent to Gemini")
    print("=" * 80)
    
    prompt = """Please provide a comprehensive summary of this document. 
Focus on:
1. Key financial metrics and results (if it's a results/concall document)
2. Important announcements or decisions
3. Management commentary or guidance
4. Any significant changes or updates
5. Key numbers, percentages, and dates

Format the summary in a clear, structured manner with bullet points where appropriate."""
    
    print(prompt)
    
    # Save prompt
    prompt_file = os.path.join(output_dir, f"{pdf_name}_04_prompt.txt")
    with open(prompt_file, 'w', encoding='utf-8') as f:
        f.write(prompt)
    print(f"\n[SAVED] Prompt: {prompt_file}")
    
    # Step 6: Summary of what gets sent
    print("\n" + "=" * 80)
    print("STEP 6: Summary - What gets sent to Gemini API")
    print("=" * 80)
    
    if processed_pdf.metadata['needs_chunking']:
        print(f"\nFor chunked documents (2-pass approach):")
        print(f"  Pass 1: Each chunk ({len(processed_pdf.chunks)} chunks) will be sent with:")
        print(f"    - Chunk text ({len(processed_pdf.chunks[0]):,} chars per chunk avg)")
        print(f"    - Chunk-specific prompt")
        print(f"  Pass 2: Combined summaries will be sent with:")
        print(f"    - All chunk summaries concatenated")
        print(f"    - Final combination prompt")
    else:
        print(f"\nFor single-chunk documents:")
        print(f"  Single request will be sent with:")
        print(f"    - Cleaned text ({len(processed_pdf.cleaned_text):,} characters)")
        print(f"    - Summary prompt")
    
    # Estimate tokens
    estimated_tokens = len(processed_pdf.cleaned_text) // 4
    print(f"\nEstimated tokens: ~{estimated_tokens:,} (rough estimate: 1 token ~ 4 characters)")
    print(f"TPM limit: 1,000,000 tokens")
    print(f"Status: {'✅ Within limit' if estimated_tokens < 1000000 else '⚠️ Exceeds limit'}")
    
    print("\n" + "=" * 80)
    print("COMPLETE - All processed data saved to files")
    print("=" * 80)
    print(f"\nOutput files saved in: {output_dir}")
    print(f"  - {pdf_name}_01_raw_extracted.txt (raw extraction)")
    print(f"  - {pdf_name}_02_cleaned.txt (cleaned text)")
    if processed_pdf.metadata['needs_chunking']:
        for i in range(1, len(processed_pdf.chunks) + 1):
            print(f"  - {pdf_name}_03_chunk_{i:02d}.txt (chunk {i})")
    else:
        print(f"  - {pdf_name}_03_single_chunk.txt (single chunk)")
    print(f"  - {pdf_name}_04_prompt.txt (prompt)")


def main():
    """Main function."""
    if len(sys.argv) < 2:
        print("Usage: python show_processed_data.py <pdf_path> [output_dir]")
        print("\nExample:")
        print('  python show_processed_data.py "C:\\Users\\rajka\\Downloads\\file.pdf"')
        return
    
    pdf_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    
    show_processed_data(pdf_path, output_dir)


if __name__ == "__main__":
    main()
