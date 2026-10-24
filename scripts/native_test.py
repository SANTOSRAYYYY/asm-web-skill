#!/usr/bin/env python3
"""Fetch the native Linux server and compare its page with the built snapshot.

The server is expected at http://127.0.0.1:8080/. It is started by the caller
(for example the GitHub Actions workflow) and killed afterwards.
"""

from __future__ import annotations

import pathlib
import sys
import time
import urllib.request

import brotli

URL = "http://127.0.0.1:8080/"
expected_path = pathlib.Path(
    sys.argv[1] if len(sys.argv) > 1 else "dist/index.page.html"
)
expected = expected_path.read_bytes()

def fetch() -> tuple[bytes, bytes, str]:
    request = urllib.request.Request(
        URL,
        headers={
            "Accept-Encoding": "br, gzip",
            "Connection": "close",
        },
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        body = response.read()
        encoding = response.headers.get("Content-Encoding", "")
        content_type = response.headers.get("Content-Type", "")
    if "br" not in encoding.lower():
        raise AssertionError(f"unexpected Content-Encoding: {encoding!r}")
    return brotli.decompress(body), body, content_type


last_error: Exception | None = None
for _ in range(30):
    try:
        # Two sequential connections prove the server restored its listening
        # fd and loops correctly after closing the first client.
        results = [fetch(), fetch()]
        for index, (page, _body, _content_type) in enumerate(results, 1):
            if page != expected:
                raise AssertionError(
                    f"connection {index}: served page mismatch: "
                    f"{len(page):,} B vs {len(expected):,} B"
                )
        _, first_body, first_type = results[0]
        print(
            f"native server PASS: {len(results)} connections, "
            f"{first_type}, {len(first_body):,} B compressed, "
            f"{len(expected):,} B page"
        )
        sys.exit(0)
    except Exception as exc:  # server may still be starting
        last_error = exc
        time.sleep(0.2)

print(f"native server FAIL: {last_error}", file=sys.stderr)
sys.exit(1)
