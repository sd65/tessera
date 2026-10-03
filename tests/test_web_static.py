"""Checks the allowlisted static UI assets and their MIME types."""

from tessera import web


def main() -> int:
    filename, content_type = web.STATIC["/theme.js"]
    assert filename == "theme.js"
    assert content_type == "text/javascript; charset=utf-8"
    assert (web.UI_DIR / filename).is_file()
    print("PASS  theme module is served as JavaScript")
    chat_filename, chat_type = web.STATIC["/components/chat.js"]
    assert chat_filename == "components/chat.js"
    assert chat_type == "text/javascript; charset=utf-8"
    assert (web.UI_DIR / chat_filename).is_file()
    print("PASS  chat component is served as JavaScript")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
