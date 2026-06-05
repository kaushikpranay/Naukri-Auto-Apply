"""
dashboard.py — Launch the Naukri Automation Dashboard.

Usage:
    python dashboard.py

Opens at http://localhost:8000
"""

import uvicorn


def main():
    print()
    print("  +------------------------------------------+")
    print("  |   Naukri Automation Dashboard             |")
    print("  |   http://localhost:8000                   |")
    print("  +------------------------------------------+")
    print()
    uvicorn.run(
        "dashboard.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
