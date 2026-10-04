"""Read-only corpus health data exposed to the UI header."""

from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from support import Suite, TempCorpus, seed
from tessera import store, summarize, transcript, web

suite = Suite()
case = suite.case


def _server():
    class QuietHandler(web.Handler):
        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


@case
def doctor_api_reports_database_counts_and_severity():
    with TempCorpus() as conn:
        success_id = seed(conn, video_id="healthy-video")
        store.record_summary(
            conn,
            success_id,
            summarize.SummaryResult(
                summarize.OK, "normalized", text="A stored summary."
            ),
        )
        failed_summary_id = seed(conn, video_id="summary-failed")
        store.record_summary(
            conn,
            failed_summary_id,
            summarize.SummaryResult(
                summarize.ERROR,
                "normalized",
                error="test summary error",
            ),
        )
        retry_id = seed(conn, video_id="retryable")
        store.record(
            conn,
            retry_id,
            transcript.TranscriptResult(transcript.ERROR, error="test transcript error"),
        )
        seed(conn, video_id="pending", status=store.PENDING)
        permanent_id = seed(conn, video_id="permanent")
        store.record(
            conn,
            permanent_id,
            transcript.TranscriptResult(
                transcript.NO_CAPTIONS, error="no captions"
            ),
        )
        conn.commit()

        server, thread = _server()
        try:
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/api/doctor", timeout=5
            ) as response:
                assert response.headers.get("Cache-Control") == "no-store"
                status = json.loads(response.read())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        assert status == {
            "state": "error",
            "videos": 5,
            "transcripts": 2,
            "transcript_records": 5,
            "summaries": 1,
            "retryable_transcripts": 1,
            "pending_transcripts": 1,
            "exhausted_transcripts": 0,
            "permanent_transcripts": 1,
            "failed_summaries": 1,
        }


@case
def a_corpus_without_errors_is_green():
    with TempCorpus() as conn:
        seed(conn)
        conn.commit()
        health = store.corpus_health(conn)
        assert health["state"] == "ok"
        assert health["videos"] == 1
        assert health["transcripts"] == 1
        assert health["permanent_transcripts"] == 0
        assert health["retryable_transcripts"] == 0


@case
def retryable_errors_make_health_yellow():
    with TempCorpus() as conn:
        video_id = seed(conn, video_id="temporarily-disabled")
        store.record(
            conn,
            video_id,
            transcript.TranscriptResult(
                transcript.DISABLED, error="no caption tracks returned"
            ),
        )
        conn.commit()
        health = store.corpus_health(conn)
        assert health["state"] == "warning"
        assert health["retryable_transcripts"] == 1
        assert health["permanent_transcripts"] == 0


if __name__ == "__main__":
    raise SystemExit(suite.run())
