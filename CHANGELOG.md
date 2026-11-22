# Changelog

## 2.4.1 (2026-11-22), first public release
- Federal orders with no numbered sections (such as SI/97-5) can now be read: ask for `order`. `preamble` works as a section name too.
- The Justice Laws "current to" line is now read when the page has no "last amended on" date.
- A cached page saved without its currency line is downloaded again instead of being reused.
- The cache moved out of the shared temp folder on Linux and macOS to `~/.cache/law-lookup-cache`, private to you. `LAW_CACHE_DIR` overrides it. Windows still uses `%TEMP%`, which is already per user.
- XML with a DTD or entities is refused (Justice Laws files have none).
- Unknown `--options` are an error now instead of being read as section numbers.
- Every result says "This copy is not an official version.", and Ontario results print the "© King's Printer for Ontario" line, as the copyright terms ask.
- `python test_law.py --offline` runs only the tests that need no network.

## 2.4.0
- The version this repo started from: e-Laws and Justice Laws lookups, phrase search, definitions, status and commencement orders, and the MCP server.
