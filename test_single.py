#!/usr/bin/env python3
"""
Quick test script to verify the PDF pipeline works correctly.
This processes a single PDF from the sample data.
"""

import sys
from pathlib import Path

# Add Script directory to path
sys.path.insert(0, str(Path(__file__).parent / "Script"))

from pdf_pipeline import main

if __name__ == "__main__":
    # Test with a single PDF
    test_pdf = "ZYZ 1月高频（139篇+29背景）/P3（41高+9次）/1. 高频/187. P3 - Petrol power an eco-revolution 交通的革命【高】/187. P3 - Petrol power an eco-revolution 交通的革命【高】.pdf"
    
    if not Path(test_pdf).exists():
        print(f"Error: Test PDF not found at {test_pdf}")
        print("Please provide a valid PDF path.")
        sys.exit(1)
    
    print("Running test with sample PDF...")
    print(f"Input: {test_pdf}")
    
    # Run with force-html and bundle-pdf flags
    sys.exit(main([
        test_pdf,
        "--limit", "1",
        "--force-html",
        "--bundle-pdf"
    ]))
