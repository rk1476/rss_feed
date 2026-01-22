"""Wrapper script to run rss_feed in test mode with file input"""
import sys
import os

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

# Mock input function to provide automated responses
_input_responses = [
    "test",  # Run mode
    "yes",   # Filter by stocks
    r"C:\Users\rajka\Downloads\cleaned_tickers.txt"  # File path
]
_input_index = 0

def mock_input(prompt):
    global _input_index
    if _input_index < len(_input_responses):
        response = _input_responses[_input_index]
        _input_index += 1
        print(prompt + response)
        return response
    return ""

# Replace input function
import builtins
original_input = builtins.input
builtins.input = mock_input

# Import and run main
from rss_feed import main

if __name__ == "__main__":
    try:
        main()
    finally:
        # Restore original input
        builtins.input = original_input
