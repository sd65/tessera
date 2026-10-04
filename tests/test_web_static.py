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

    transcript_source = (web.UI_DIR / "components/transcript.js").read_text(encoding="utf-8")
    player_source = (web.UI_DIR / "components/video-player.js").read_text(encoding="utf-8")
    app_source = (web.UI_DIR / "components/app.js").read_text(encoding="utf-8")
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
    assert "--bottom-bar-height: 70px" in styles
    assert ".pager {" in styles and "flex: 0 0 var(--bottom-bar-height)" in styles
    assert ".chat-form {" in styles and "height: var(--bottom-bar-height)" in styles
    assert "el('strong', 'info-title', 'Info')" in transcript_source
    assert transcript_source.index("el('strong', 'info-title', 'Info')") < transcript_source.index(
        "el('span', 'info-meta')"
    )
    info_style = styles.split(".info-title {", 1)[1].split("}", 1)[0]
    assert "text-transform: uppercase" not in info_style
    assert transcript_source.index("this.#body.append(box)") < transcript_source.index(
        "this.#body.append(passages)"
    )
    print("PASS  pane controls align, transcript header shows Info, passages follow summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
