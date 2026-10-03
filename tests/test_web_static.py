"""Checks the allowlisted static UI assets and their MIME types."""

from pathlib import Path

from tessera import web


def main() -> int:
    filename, content_type = web.STATIC["/theme.js"]
    assert filename == "theme.js"
    assert content_type == "text/javascript; charset=utf-8"
    assert (web.UI_DIR / filename).is_file()
    print("PASS  theme module is served as JavaScript")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
