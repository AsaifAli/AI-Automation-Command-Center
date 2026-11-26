"""Small HTTP health check for local and deployed service endpoints."""

import sys
from urllib.parse import urlsplit

import httpx


def main() -> int:
    url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "http://127.0.0.1:8000/health"
    )
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        expected_ports = {None, 443} if scheme == "https" else {None, 80}
        if (
            scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.port not in expected_ports
        ):
            raise ValueError("Health check URL must be a valid HTTP(S) URL.")

        with httpx.Client(
            timeout=5.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream("GET", url) as response:
                if response.status_code != 200:
                    print(
                        f"health check failed: HTTP {response.status_code}",
                        file=sys.stderr,
                    )
                    return 1

    except (ValueError, httpx.HTTPError) as exc:
        print(f"health check failed: {type(exc).__name__}", file=sys.stderr)
        return 1

    print(f"healthy: {url}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
