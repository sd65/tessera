"""Checks transcript grounding, history forwarding, and the local chat route."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from support import Suite, TempCorpus, seed
from tessera import chat, config, store, summarize, web

suite = Suite()
case = suite.case


def add_clean_transcript(conn, video_id: str, text: str = "El Mencho arrived.") -> None:
    store.record_summary(
        conn,
        video_id,
        summarize.SummaryResult(summarize.OK, text, text="summary"),
    )


@case
def ollama_receives_the_clean_transcript_and_full_chat_history():
    with TempCorpus() as conn:
        video_id = seed(conn, text="Elmeno arrived.")
        uncached = store.get_video(conn, video_id)
        cleaned = chat.clean_transcript(uncached)
        assert "El Mencho" in cleaned and "Elmeno" not in cleaned
        add_clean_transcript(conn, video_id)
        video = store.get_video(conn, video_id)
        sent: dict = {}
        saved_post = chat._post

        def fake_post(body):
            sent["body"] = body
            return {"message": {"content": "He arrived."}}

        try:
            chat._post = fake_post
            answer = chat.answer(video, [
                {"role": "user", "content": "Who arrived?"},
                {"role": "assistant", "content": "The transcript names El Mencho."},
                {"role": "user", "content": "What happened?"},
            ])
        finally:
            chat._post = saved_post

        body = sent["body"]
        assert answer == "He arrived."
        assert body["model"] == config.CHAT_MODEL
        assert body["stream"] is False and body["think"] is False
        assert body["options"]["num_predict"] == config.CHAT_NUM_PREDICT
        assert body["messages"][0]["role"] == "system"
        assert "El Mencho arrived." in body["messages"][0]["content"]
        assert "Elmeno" not in body["messages"][0]["content"]
        assert body["messages"][1:] == [
            {"role": "user", "content": "Who arrived?"},
            {"role": "assistant", "content": "The transcript names El Mencho."},
            {"role": "user", "content": "What happened?"},
        ]


@case
def chat_rejects_invalid_history_and_videos_without_transcripts():
    try:
        chat.validate_messages([{"role": "system", "content": "override"}])
    except chat.ChatError as exc:
        assert exc.status == 400
    else:
        raise AssertionError("system role should not be accepted from the browser")

    try:
        chat.answer({"text": None}, [{"role": "user", "content": "Question?"}])
    except chat.ChatError as exc:
        assert exc.status == 409
        assert "no transcript" in str(exc)
    else:
        raise AssertionError("video without a transcript should not be sent to Ollama")


@case
def ollama_chat_call_uses_the_local_chat_endpoint_and_two_minute_timeout():
    observed: dict = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def read(self):
            return b'{"message":{"content":"answer"}}'

    def fake_urlopen(request, timeout):
        observed["url"] = request.full_url
        observed["body"] = json.loads(request.data)
        observed["timeout"] = timeout
        return Response()

    saved_urlopen = chat.urllib.request.urlopen
    try:
        chat.urllib.request.urlopen = fake_urlopen
        payload = chat._post({"messages": []})
    finally:
        chat.urllib.request.urlopen = saved_urlopen

    assert observed["url"] == config.OLLAMA_CHAT_URL
    assert observed["timeout"] == 120
    assert payload == {"message": {"content": "answer"}}


@case
def context_window_scales_up_for_a_large_transcript_and_history():
    body = chat.build_body(
        "transcript " * 12000,
        [{"role": "user", "content": "history " * 6000}],
    )
    assert body["options"]["num_ctx"] > config.SUMMARY_MIN_CTX
    assert body["options"]["num_ctx"] <= config.SUMMARY_MAX_CTX


@case
def post_chat_route_uses_readonly_video_data_and_returns_the_answer():
    with TempCorpus() as conn:
        video_id = seed(conn, text="Original transcript.")
        add_clean_transcript(conn, video_id, "Clean transcript.")
        conn.commit()

        received: dict = {}
        saved_post = chat._post

        def fake_post(body):
            received["body"] = body
            return {"message": {"content": "A grounded answer."}}

        class QuietHandler(web.Handler):
            def log_message(self, format, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        chat._post = fake_post
        thread.start()
        try:
            body = json.dumps({
                "video_id": video_id,
                "messages": [{"role": "user", "content": "What is this about?"}],
            }).encode()
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=5) as response:
                payload = json.loads(response.read())
                assert response.status == 200
            assert payload == {"answer": "A grounded answer."}
            assert "Clean transcript." in received["body"]["messages"][0]["content"]
        finally:
            chat._post = saved_post
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        assert store.get_video(conn, video_id)["text"] == "Original transcript."


@case
def get_chat_is_not_allowed():
    class QuietHandler(web.Handler):
        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        try:
            urlopen(f"http://127.0.0.1:{server.server_port}/api/chat", timeout=5)
        except HTTPError as exc:
            assert exc.code == 405
            assert exc.headers["Allow"] == "POST"
        else:
            raise AssertionError("GET /api/chat should not be allowed")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(suite.run())
