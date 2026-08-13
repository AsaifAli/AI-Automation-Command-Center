import sys
import urllib.request

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/health"
with urllib.request.urlopen(url, timeout=5) as response:
    if response.status != 200:
        raise SystemExit(1)
print(f"healthy: {url}")
