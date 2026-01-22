"""Quick test script for POC components"""
import sys
import os

print("=" * 80)
print("POC Component Test")
print("=" * 80)

# Test imports
print("\n1. Testing imports...")
try:
    from gemini_api import load_gemini_api_key, setup_gemini_client
    print("   [OK] gemini_api imports successful")
except Exception as e:
    print(f"   [ERROR] gemini_api import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

try:
    from pdf_processor import process_pdf, PDFPLUMBER_AVAILABLE, PYPDF_AVAILABLE, PDFMINER_AVAILABLE
    print("   [OK] pdf_processor imports successful")
except Exception as e:
    print(f"   [ERROR] pdf_processor import failed: {e}")
    sys.exit(1)

# Test API key loading
print("\n2. Testing API key loading...")
api_key = load_gemini_api_key()
if api_key:
    print(f"   [OK] API key found (length: {len(api_key)})")
    print(f"   [OK] API key starts with: {api_key[:10]}...")
else:
    print("   [ERROR] API key not found!")
    sys.exit(1)

# Test PDF libraries
print("\n3. Testing PDF libraries...")
print(f"   pdfplumber: {'[OK] Available' if PDFPLUMBER_AVAILABLE else '[WARNING] Not installed'}")
print(f"   pypdf: {'[OK] Available' if PYPDF_AVAILABLE else '[WARNING] Not installed'}")
print(f"   pdfminer: {'[OK] Available' if PDFMINER_AVAILABLE else '[WARNING] Not installed'}")

if not (PDFPLUMBER_AVAILABLE or PYPDF_AVAILABLE or PDFMINER_AVAILABLE):
    print("   [ERROR] No PDF libraries available!")
    sys.exit(1)

# Test Gemini client setup (without actually calling API)
print("\n4. Testing Gemini client setup...")
try:
    client = setup_gemini_client()
    print("   [OK] Gemini client configured successfully")
    print(f"   [OK] Client type: {type(client).__name__}")
except Exception as e:
    print(f"   [ERROR] Gemini client setup failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "=" * 80)
print("[SUCCESS] All components are ready!")
print("=" * 80)
print("\nTo test with a PDF file, run:")
print("  python gemini_api.py \"path/to/your/file.pdf\"")
