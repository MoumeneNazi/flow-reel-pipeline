#!/usr/bin/env python3
"""
Flow Reel Recreation Studio - Launcher
Starts the WebUI server and automatically opens the browser.
"""

import os
import sys
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    url = f"http://127.0.0.1:{port}"
    print(f"\n🚀 Launching Flow Reel Recreation Studio at {url}...")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    import uvicorn
    from web_server import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
