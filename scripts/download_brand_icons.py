#!/usr/bin/env python3
"""
Download brand favicons (Google s2) into server/static/icons/brand/

Run from repo root: python scripts/download_brand_icons.py

When adding MODEL_SLUG_FIRST_SEGMENT_TO_BRAND_HOST in chat.js, add a row here.
Second column: optional alternate domain if Google favicon lookup 404 for the canonical host.

See also: ../../server/static/icons/brand/ (PNG filenames: host dots -> hyphens).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import quote

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "server" / "static" / "icons" / "brand"

# (canonical host matching chat.js maps -> optional fetch-domain override)
CANONICAL_FETCH_OVERRIDES: list[tuple[str, str | None]] = [
    ("openrouter.ai", None),
    ("openai.com", None),
    ("anthropic.com", None),
    ("google.com", None),
    ("meta.com", None),
    ("mistral.ai", None),
    ("deepseek.com", None),
    ("alibaba.com", None),
    ("bytedance.com", "tiktok.com"),
    ("nvidia.com", None),
    ("poolside.ai", None),
    ("minimax.chat", "minimax.io"),
    ("kuaishou.com", None),
    ("x.ai", None),
    ("moonshot.cn", None),
    ("perplexity.ai", None),
    ("xiaomi.com", None),
    ("baai.ac.cn", "www.baai.ac.cn"),
]


def filename_for_canonical(canonical_domain: str) -> str:
    safe = canonical_domain.replace(".", "-")
    return f"{safe}.png"


def google_favicon_url(domain: str, size: int = 64) -> str:
    return f"https://www.google.com/s2/favicons?domain={quote(domain, safe='')}&sz={size}"


def main() -> int:
    p = argparse.ArgumentParser(description="Download brand icons into static/icons/brand/")
    p.add_argument(
        "--extra",
        nargs="*",
        metavar="PAIR",
        help='extra "canonical[,fetch]" e.g. cohere.com,cohere.com',
    )
    args = p.parse_args()

    entries = list(CANONICAL_FETCH_OVERRIDES)
    for raw in args.extra or []:
        parts = raw.split(",", 1)
        can = parts[0].strip()
        fetch = parts[1].strip() if len(parts) > 1 else None
        if can:
            entries.append((can, fetch or None))

    seen: set[str] = set()
    deduped: list[tuple[str, str | None]] = []
    for can, fetch in entries:
        if can in seen:
            continue
        seen.add(can)
        deduped.append((can, fetch))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ua = {"User-Agent": "IIProxyBrandIcons/1.0 (+scripts/download_brand_icons.py)"}

    ok = 0
    fail = []
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=ua) as client:
        for canonical, fetch_ov in deduped:
            fetch_dom = fetch_ov or canonical
            path = OUT_DIR / filename_for_canonical(canonical)
            url = google_favicon_url(fetch_dom)
            try:
                r = client.get(url)
                r.raise_for_status()
                if len(r.content) < 16:
                    fail.append(canonical)
                    print("[skip tiny]", canonical, "<-", fetch_dom)
                    continue
                path.write_bytes(r.content)
                print("[ok]", canonical, "<-", fetch_dom, "->", path.relative_to(REPO_ROOT))
                ok += 1
            except Exception as e:
                fail.append(canonical)
                print("[fail]", canonical, "<-", fetch_dom, repr(e))

    print("")
    print("done:", ok, "/", len(deduped), "files ->", OUT_DIR)
    if fail:
        print("failed:", ",".join(fail), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
