"""Test script: Single Gemini request with no retries"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from pdf_processor import process_pdf
from gemini_api import setup_gemini_client, enforce_rate_limit

# Setup
pdf_path = r"C:\Users\rajka\Downloads\ANANDRATHI_12012026185857_OutcomeSD.pdf"
model_name = "gemini-2.5-flash-lite"  # Changed from gemini-2.0-flash-exp

print("=" * 80)
print("SINGLE REQUEST TEST (No Retries)")
print("=" * 80)

# Preprocess PDF
print("\n1. Preprocessing PDF...")
processed_pdf = process_pdf(pdf_path)
print(f"   Pages: {processed_pdf.metadata['page_count']}")
print(f"   Characters: {processed_pdf.metadata['character_count']:,}")
print(f"   Chunks: {processed_pdf.metadata['chunk_count']}")

# Setup client
print("\n2. Setting up Gemini client...")
client = setup_gemini_client()

# Prepare prompt
prompt = """Please provide a comprehensive summary of this document. 
Focus on:
1. Key financial metrics and results (if it's a results/concall document)
2. Important announcements or decisions
3. Management commentary or guidance
4. Any significant changes or updates
5. Key numbers, percentages, and dates

Format the summary in a clear, structured manner with bullet points where appropriate."""

# Make single request (no retries)
print("\n3. Making single Gemini API request (no retries)...")
print(f"   Model: {model_name}")
print(f"   Text length: {len(processed_pdf.cleaned_text):,} characters")

request_count = 0

try:
    # Enforce rate limit
    enforce_rate_limit()
    
    # Make the request
    request_count += 1
    print(f"\n   [REQUEST #{request_count}] Calling client.models.generate_content()...")
    
    response = client.models.generate_content(
        model=model_name,
        contents=[processed_pdf.cleaned_text, prompt]
    )
    
    print(f"   [SUCCESS] Request completed!")
    print(f"   Response type: {type(response)}")
    print(f"   Response text length: {len(response.text):,} characters")
    print(f"\n   " + "=" * 76)
    print("   FULL RESPONSE:")
    print("   " + "=" * 76)
    print(response.text)
    print("   " + "=" * 76)
    
except Exception as e:
    print(f"   [ERROR] Request failed!")
    print(f"   Error type: {type(e).__name__}")
    print(f"   Error message: {str(e)[:500]}")
    if len(str(e)) > 500:
        print("   ...")

print(f"\n" + "=" * 80)
print(f"TOTAL REQUESTS SENT: {request_count}")
print("=" * 80)
