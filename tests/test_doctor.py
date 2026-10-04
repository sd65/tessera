"""Diagnostic and explicit retry behavior for ``tessera doctor``."""

from __future__ import annotations

from support import Suite, TempCorpus, seed
from tessera import cli, config, store, summarize, transcript

suite = Suite()
case = suite.case


def fail_transcript(conn, video_id, status=transcript.ERROR):
    store.record(
        conn,
        video_id,
        transcript.TranscriptResult(status, error=f"test {status}"),
    )


def fail_summary(conn, video_id):
    for _ in range(config.SUMMARY_MAX_ATTEMPTS):
        store.record_summary(
            conn,
            video_id,
            summarize.SummaryResult(
                summarize.ERROR, "stored normalized text", error="test summary failure"
            ),
        )


@case
def doctor_reports_errors_without_mutating_the_corpus():
    with TempCorpus() as conn:
        exhausted_id = seed(conn, video_id="transcript-error")
        for _ in range(config.MAX_ATTEMPTS):
            fail_transcript(conn, exhausted_id)
        permanent_id = seed(conn, video_id="permanent-error")
        fail_transcript(conn, permanent_id, transcript.NO_CAPTIONS)
        summary_id = seed(conn, video_id="summary-error")
        fail_summary(conn, summary_id)
        conn.commit()

        before = {
            row["video_id"]: row["attempts"]
            for row in conn.execute("SELECT video_id, attempts FROM transcripts")
        }
        cli.cmd_doctor()
        after = {
            row["video_id"]: row["attempts"]
            for row in conn.execute("SELECT video_id, attempts FROM transcripts")
        }

        assert before == after
        assert [row["video_id"] for row in store.select_retryable(
            conn, include_exhausted=True
        )] == [exhausted_id]
        assert [row["video_id"] for row in store.select_permanent_failures(conn)] == [
            permanent_id
        ]
        assert [row["video_id"] for row in store.select_summary_errors(conn)] == [
            summary_id
        ]


@case
def doctor_retry_includes_exhausted_transcripts_and_summaries_but_skips_permanent_errors():
    saved_min, saved_max, saved_limit = (
        config.FETCH_INTERVAL_MIN,
        config.FETCH_INTERVAL_MAX,
        config.FETCH_HOURLY_LIMIT,
    )
    saved_fetch = cli.transcript.fetch_transcript
    saved_summarize = cli._summarize
    config.FETCH_INTERVAL_MIN = config.FETCH_INTERVAL_MAX = 0
    config.FETCH_HOURLY_LIMIT = 100
    try:
        with TempCorpus() as conn:
            transcript_id = seed(conn, video_id="transcript-error")
            for _ in range(config.MAX_ATTEMPTS):
                fail_transcript(conn, transcript_id)
            permanent_id = seed(conn, video_id="permanent-error")
            fail_transcript(conn, permanent_id, transcript.NO_CAPTIONS)
            summary_id = seed(conn, video_id="summary-error")
            fail_summary(conn, summary_id)
            conn.commit()

            fetched = []
            summarized = []

            def fetch(video_id):
                fetched.append(video_id)
                return transcript.TranscriptResult(
                    transcript.OK, lang="en", kind="auto", text="fresh transcript"
                )

            def summarize_one(db, video_id, *, force=False):
                summarized.append((video_id, force))
                result = summarize.SummaryResult(
                    summarize.OK,
                    "fresh normalized transcript",
                    text="1. SUBJECT\nA stored test summary.",
                )
                store.record_summary(db, video_id, result)
                return result

            cli.transcript.fetch_transcript = fetch
            cli._summarize = summarize_one
            result = cli.cmd_doctor(retry=True)

            assert result == 0
            assert fetched == [transcript_id]
            assert permanent_id not in fetched
            assert (summary_id, True) in summarized
            assert (transcript_id, False) in summarized
            assert store.get_video(conn, summary_id)["summary_status"] == summarize.OK
            assert store.get_video(conn, transcript_id)["status"] == transcript.OK
            assert store.select_permanent_failures(conn)[0]["video_id"] == permanent_id
    finally:
        config.FETCH_INTERVAL_MIN = saved_min
        config.FETCH_INTERVAL_MAX = saved_max
        config.FETCH_HOURLY_LIMIT = saved_limit
        cli.transcript.fetch_transcript = saved_fetch
        cli._summarize = saved_summarize


if __name__ == "__main__":
    raise SystemExit(suite.run())
