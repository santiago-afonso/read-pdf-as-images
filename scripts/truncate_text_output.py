#!/usr/bin/env python
from __future__ import annotations

import argparse
import math
from pathlib import Path


def _fallback_token_count(text: str) -> int:
    # Lightweight approximation used only if tiktoken is unavailable.
    return max(1, math.ceil(len(text) / 4))


def _load_counter():
    try:
        import tiktoken  # type: ignore
    except Exception:
        return _fallback_token_count, "approx_chars_div4"

    enc = tiktoken.get_encoding("o200k_base")
    return lambda text: len(enc.encode(text)), "tiktoken:o200k_base"


def _truncate_to_token_limit(text: str, limit: int, token_count) -> str:
    if token_count(text) <= limit:
        return text

    lo = 0
    hi = len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid]
        if token_count(candidate) <= limit:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Truncate text to a max token budget and append a truncation note that includes "
            "estimated total tokens."
        )
    )
    parser.add_argument("--input", required=True, help="Path to input text file.")
    parser.add_argument(
        "--max-tokens",
        type=int,
        required=True,
        help="Maximum output tokens. 0 disables truncation.",
    )
    args = parser.parse_args(argv)

    text = Path(args.input).read_text(encoding="utf-8", errors="replace")
    max_tokens = args.max_tokens
    if max_tokens < 0:
        raise SystemExit("--max-tokens must be >= 0")

    token_count, method = _load_counter()
    estimated_total_tokens = token_count(text)

    if max_tokens == 0 or estimated_total_tokens <= max_tokens:
        print(text, end="")
        return 0

    notice = (
        "\n\n[read-pdf output truncated]\n"
        f"Estimated total tokens for this response: {estimated_total_tokens} ({method}).\n"
        f"Configured max output tokens: {max_tokens}. "
        "Override with --max-output-tokens <N> (0 disables truncation).\n"
    )
    notice_tokens = token_count(notice)
    available_for_body = max(1, max_tokens - notice_tokens)

    truncated_body = _truncate_to_token_limit(text, available_for_body, token_count).rstrip()
    final_output = truncated_body + notice
    print(final_output, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
