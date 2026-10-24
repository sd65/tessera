"""Checks the allowlisted static UI assets and their MIME types."""

import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from tessera import web


def main() -> int:
    favicon_filename, favicon_type = web.STATIC["/favicon.svg"]
    assert favicon_filename == "favicon.svg"
    assert favicon_type == "image/svg+xml; charset=utf-8"
    assert (web.UI_DIR / favicon_filename).is_file()
    html = (web.UI_DIR / "index.html").read_text(encoding="utf-8")
    app_source = (web.UI_DIR / "components/app.js").read_text(encoding="utf-8")
    video_list_source = (web.UI_DIR / "components/video-list.js").read_text(encoding="utf-8")
    styles = (web.UI_DIR / "styles.js").read_text(encoding="utf-8")
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

    class QuietHandler(web.Handler):
        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/components/video-list.js",
            timeout=5,
        ) as response:
            video_list_response = response.read().decode("utf-8")
            assert response.headers.get("Cache-Control") == "no-store"
        assert "recommendation-dot" in video_list_response
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    print("PASS  updated UI scripts are served without cache")

    assert "/api/doctor" in app_source
    assert "aria-describedby=\"health-tooltip\"" in app_source
    assert ".health-wrap:hover .health-tooltip" in styles
    assert ".health-wrap:focus-within .health-tooltip" in styles
    assert ".health-tooltip[hidden] { display: none; }" in styles
    assert "warning: 'Retryable work'" in app_source
    assert "error: 'Permanent errors'" in app_source
    assert '.health-indicator[data-state="warning"]' in styles
    assert '.health-indicator[data-state="error"]' in styles
    print("PASS  database health indicator is hover and keyboard accessible")

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
    assert "autoplay: '0'" in player_source
    assert "cc_load_policy: '0'" in player_source
    assert "hl: 'en'" in player_source
    assert "embedUrl(video.video_id)" in player_source
    assert "embedUrl(videoId, startSeconds)" in player_source
    assert "this.#loadedVideoId !== video.video_id" in player_source
    assert "el('button', 'chat-citation passage', '')" in transcript_source
    assert "(~${watchDuration(item.duration_ms)})" in transcript_source
    assert "Math.ceil(durationMs / 1000)" in transcript_source
    assert "title.textContent = item.label" in transcript_source
    print("PASS  passage links emit safe, integer-second embed seeks")

    assert ".player .placeholder[hidden] {\n  display: none;" in styles
    assert "--bottom-bar-height: 52px" in styles
    assert ".pager {" in styles and "flex: 0 0 var(--bottom-bar-height)" in styles
    assert ".chat-form {" in styles and "height: var(--bottom-bar-height)" in styles
    assert ".transcript-toggle {" in styles and "height: var(--bottom-bar-height)" in styles
    chat_source = (web.UI_DIR / "components/chat.js").read_text(encoding="utf-8")
    assert "el('button', 'chat-citation'" in chat_source
    assert "new CustomEvent('seek-video'" in chat_source
    assert "this.#player.addEventListener('seek-video', seekVideo)" in app_source
    assert "const history = state.messages.map(({ role, content }) => ({ role, content }))" in app_source
    assert ".chat-citation:focus-visible" in styles

    # Read markers: the endpoint, the payload columns, and the row DOM that the
    # styling hangs off. Asserted here because these are wiring facts a static
    # read of the sources settles, and a rename in any of the three would
    # otherwise only show up as a sidebar that never bolds anything.
    assert "/api/read" in app_source
    assert "this.#list.addEventListener('read-toggle'" in app_source
    assert "this.#transcript.addEventListener('summary-view'" in app_source
    assert "rows.filter((row) => row.read_at).map((row) => row.video_id)" in app_source
    assert "const READ_AFTER_MS = 1_000;" in app_source
    assert "document.addEventListener('visibilitychange', this.#onVisibility)" in app_source
    assert "document.removeEventListener('visibilitychange', this.#onVisibility)" in app_source
    # The timer must be cancelled on selection, or a click through the sidebar
    # marks the video just left behind.
    assert app_source.count("this.#stopReadTimer()") >= 2

    # A row is a div of two sibling buttons: nesting the toggle inside the row
    # button would be invalid HTML.
    assert "const container = el('div', 'row');" in video_list_source
    assert "const button = el('button', 'row-open');" in video_list_source
    assert "const toggle = el('button', 'row-read');" in video_list_source
    assert "container.append(button, toggle)" in video_list_source
    assert "this.#rowsBox.querySelectorAll('.row')" in video_list_source
    assert "row.querySelector('.row-open')" in video_list_source
    # No dot: weight alone marks an unread row.
    assert "unread-dot" not in video_list_source
    assert "unread-dot" not in styles
    assert "toggle.setAttribute('aria-pressed', String(read))" in video_list_source
    assert "event.stopPropagation()" in video_list_source
    assert "new CustomEvent('read-toggle'" in video_list_source
    assert "queueMicrotask(() => this.#announceSummary(summaryVideoId))" in transcript_source
    assert "queueMicrotask(() => this.#announceSummary(null))" in transcript_source
    assert "new CustomEvent('summary-view'" in transcript_source
    assert "if (!videoId) return;" in app_source

    # The reveal must not use visibility, which would drop the button out of the
    # tab order and make it keyboard-unreachable.
    assert ".row-read {" in styles and "opacity: 0;" in styles
    assert ".row:hover .row-read,\n.row:has(:focus-visible) .row-read {" in styles
    # Scoped to the two selectors that reveal the toggle: :focus-within would also
    # match the pointer focus a row click leaves on .row-open, pinning the toggle
    # open on the selected row. The health tooltip above legitimately wants the
    # looser match, so this is not a blanket ban on the pseudo-class — and the
    # rules are read without their comments, which name it as the thing avoided.
    reveal_rule = styles.split(".row:hover .row-read,", 1)[1].split("}", 1)[0]
    assert ":focus-within" not in reveal_rule
    assert "visibility: hidden" not in styles.split(".row-read {", 1)[1].split("}", 1)[0]
    assert '.row[data-read="false"] .title { font-weight: 750; }' in styles
    assert '.row-open[aria-current="true"] { border-left-color: var(--accent); }' in styles
    assert '.row[data-current="true"] { background: var(--accent-soft); }' in styles
    assert 'row.dataset.current = String(current)' in video_list_source
    assert '".row[aria-current' not in video_list_source
    print("PASS  read markers wire the endpoint, the timer and the row DOM")

    assert "row.watch_recommendation" in video_list_source
    assert "dot.setAttribute('aria-label', label)" in video_list_source
    assert "recommendation-dot[data-recommendation=\"full_video_recommended\"]" in styles
    assert "--recommend-passages:" in styles and "--recommend-full:" in styles
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
    assert "function renderSummary(text, passages, videoId, languageCode)" in transcript_source
    assert "sectionNumber === '5' && /^b\\)\\s*/iu.test(trimmed)" in transcript_source
    assert "recommendation.append(passageList)" in transcript_source
    assert "this.#body.append(passages)" not in transcript_source
    assert "el('h2', 'info-title summary-title', 'Summary')" in transcript_source
    assert "function formatVideoDuration(seconds)" in transcript_source
    assert "video.duration_seconds" in transcript_source
    assert "duration: 'Duration'" in transcript_source
    assert "duration: 'Durée'" in transcript_source
    assert "passages: 'Passages to watch'" not in transcript_source
    assert "passages: 'Passages à voir'" not in transcript_source
    assert ".passages { margin: -2px 0 12px; }" in styles
    assert ".summary-text .passages ul {" in styles
    summary_style = styles.split(".summary-text {", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 auto;" in summary_style
    assert "max-height" not in summary_style
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
