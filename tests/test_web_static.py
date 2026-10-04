"""Checks the allowlisted static UI assets and their MIME types."""

from tessera import web


def main() -> int:
    favicon_filename, favicon_type = web.STATIC["/favicon.svg"]
    assert favicon_filename == "favicon.svg"
    assert favicon_type == "image/svg+xml; charset=utf-8"
    assert (web.UI_DIR / favicon_filename).is_file()
    html = (web.UI_DIR / "index.html").read_text(encoding="utf-8")
    app_source = (web.UI_DIR / "components/app.js").read_text(encoding="utf-8")
    assert '<link rel="icon" type="image/svg+xml" href="/favicon.svg">' in html
    assert '<img src="/favicon.svg" alt="" aria-hidden="true">' in app_source
    print("PASS  SVG favicon is served and shown in the masthead")

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
    search_filename, search_type = web.STATIC["/transcript-search.js"]
    assert search_filename == "transcript-search.js"
    assert search_type == "text/javascript; charset=utf-8"
    assert (web.UI_DIR / search_filename).is_file()

    transcript_source = (web.UI_DIR / "components/transcript.js").read_text(encoding="utf-8")
    player_source = (web.UI_DIR / "components/video-player.js").read_text(encoding="utf-8")
    assert "new CustomEvent('seek-video'" in transcript_source
    assert "composed: true" in transcript_source
    assert "this.#player.seekTo(videoId, startMs)" in app_source
    assert "Number.isSafeInteger(startMs)" in player_source
    assert "Math.floor(startMs / 1000)" in player_source
    assert "?start=${startSeconds}" in player_source
    assert "this.#loadedVideoId !== video.video_id" in player_source
    assert "~${watchDuration(item.duration_ms)}" in transcript_source
    print("PASS  passage links emit safe, integer-second embed seeks")

    styles = (web.UI_DIR / "styles.js").read_text(encoding="utf-8")
    assert ".player .placeholder[hidden] {\n  display: none;" in styles
    assert "--bottom-bar-height: 52px" in styles
    assert ".pager {" in styles and "flex: 0 0 var(--bottom-bar-height)" in styles
    assert ".chat-form {" in styles and "height: var(--bottom-bar-height)" in styles
    assert ".transcript-toggle {" in styles and "height: var(--bottom-bar-height)" in styles
    chat_source = (web.UI_DIR / "components/chat.js").read_text(encoding="utf-8")
    assert '<input type="text" aria-label="Your question"' in chat_source
    assert ".chat-form input {" in styles and "height: 34px" in styles
    assert ".chat > header {" in styles and "color: var(--accent);" in styles.split(
        ".chat > header {", 1
    )[1].split("}", 1)[0]
    assert "color: var(--accent);" in styles.split(
        ".transcript-toggle {", 1
    )[1].split("}", 1)[0]
    for selector in (".transcript-toggle {", ".transcript-title {"):
        style = styles.split(selector, 1)[1].split("}", 1)[0]
        assert "font-size: 11px;" in style
        assert "font-weight: 700;" in style
        assert "letter-spacing: 0.05em;" in style
    assert "background: var(--panel);" in styles.split(
        ".transcript-toggle {", 1
    )[1].split("}", 1)[0]
    assert ".transcript-toggle::before" not in styles
    assert ".columns > tessera-video-player,\n.columns > tessera-transcript {\n  border-left:" in styles
    assert "el('strong', 'info-title', 'Info')" in transcript_source
    assert transcript_source.index("el('strong', 'info-title', 'Info')") < transcript_source.index(
        "el('span', 'info-meta')"
    )
    info_style = styles.split(".info-title {", 1)[1].split("}", 1)[0]
    assert "text-transform: uppercase" not in info_style
    assert transcript_source.index("this.#body.append(box)") < transcript_source.index(
        "this.#body.append(passages)"
    )
    assert "el('h2', 'info-title summary-title', 'Summary')" in transcript_source
    assert "passages: 'Passages to watch'" in transcript_source
    assert "passages: 'Passages à voir'" in transcript_source
    search_source = (web.UI_DIR / "transcript-search.js").read_text(encoding="utf-8")
    assert "normalize('NFD')" in search_source
    assert "replace(/\\p{M}/gu, '')" in search_source
    assert "findTranscriptMatches(text, query)" in transcript_source
    assert "matches[activeIndex].scrollIntoView({ block: 'center' })" in transcript_source
    assert "transcript-panel.open" in styles
    print("PASS  pane layout, summary label, localized passages and transcript search")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
