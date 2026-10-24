"""Checks the read_state table, the one write the browser UI is allowed to make.

Covers the store contract (round-trip, absent means unread, delete to unmark) and
the read endpoint on a throwaway corpus, including the validation that turns a bad
request into a 400 or a 404 rather than a 500.

Two suites rather than one, because `support.TempCorpus` redirects
`config.DB_PATH` for the duration of its `with` block and `Suite.run` has to
execute inside it — a case that ran after the block would be talking to a
deleted file.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import support
from tessera import config, store, web

# Well-formed as a YouTube id but not in the corpus: the 404 case, distinct from
# a malformed id and from a foreign-key violation, which would be a 500.
ABSENT_ID = "ZZZZZZZZZZZ"


class QuietHandler(web.Handler):
    def log_message(self, format, *args):
        pass


def _post(origin: str, path: str, payload: object = None, *,
          raw: bytes | None = None, content_type: str = "application/json") -> tuple[int, dict]:
    """POST and return (status, decoded body). An HTTP error is a result here."""
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{origin}{path}",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def _store_cases(suite: support.Suite) -> None:
    @suite.case
    def init_creates_the_table():
        # An empty database is the strictest version of "predates this feature":
        # there is no table to migrate from and no default to backfill.
        with support.TempCorpus() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(read_state)")}
            assert columns == {"video_id", "read_at"}, columns

    @suite.case
    def absence_means_unread():
        # The reason there is no boolean column: a corpus that predates the table
        # must come up entirely unread with no backfill script.
        with support.TempCorpus() as conn:
            video_id = support.seed(conn)
            assert store.list_videos(conn)["rows"][0]["read_at"] is None
            assert store.get_video(conn, video_id)["read_at"] is None

    @suite.case
    def marking_round_trips_through_both_payloads():
        with support.TempCorpus() as conn:
            video_id = support.seed(conn)
            stamp = store.set_read_state(conn, video_id, True)
            assert stamp.endswith("Z"), stamp
            assert store.list_videos(conn)["rows"][0]["read_at"] == stamp
            assert store.get_video(conn, video_id)["read_at"] == stamp

            store.set_read_state(conn, video_id, False)
            assert store.list_videos(conn)["rows"][0]["read_at"] is None
            assert store.get_video(conn, video_id)["read_at"] is None

    @suite.case
    def marking_twice_keeps_one_row():
        with support.TempCorpus() as conn:
            video_id = support.seed(conn)
            store.set_read_state(conn, video_id, True)
            store.set_read_state(conn, video_id, True)
            count = conn.execute("SELECT COUNT(*) FROM read_state").fetchone()[0]
            assert count == 1, count

    @suite.case
    def unmarking_an_unread_video_is_not_an_error():
        # What the toggle emits when a timer and a click disagree about a row.
        with support.TempCorpus() as conn:
            video_id = support.seed(conn)
            store.set_read_state(conn, video_id, False)
            assert store.list_videos(conn)["rows"][0]["read_at"] is None

    @suite.case
    def mark_all_read_is_filtered_and_preserves_existing_timestamps():
        with support.TempCorpus() as conn:
            already_read = support.seed(conn)
            other_channel = support.seed(conn, video_id="vid00000002")
            conn.execute("UPDATE videos SET channel_id = 'other' WHERE video_id = ?", (other_channel,))
            unread_same_channel = support.seed(conn, video_id="vid00000003")
            pending = support.seed(
                conn,
                video_id="vid00000004",
                status=store.PENDING,
            )
            existing_stamp = store.set_read_state(conn, already_read, True)

            assert store.mark_all_read(conn, "chan") == 1
            assert conn.execute(
                "SELECT read_at FROM read_state WHERE video_id = ?", (already_read,)
            ).fetchone()[0] == existing_stamp
            assert conn.execute(
                "SELECT read_at FROM read_state WHERE video_id = ?", (unread_same_channel,)
            ).fetchone()[0]
            assert conn.execute(
                "SELECT 1 FROM read_state WHERE video_id = ?", (other_channel,)
            ).fetchone() is None
            assert conn.execute(
                "SELECT 1 FROM read_state WHERE video_id = ?", (pending,)
            ).fetchone() is None

            assert store.mark_all_read(conn) == 1
            assert conn.execute(
                "SELECT read_at FROM read_state WHERE video_id = ?", (other_channel,)
            ).fetchone()[0]
            assert store.mark_all_read(conn) == 0

    @suite.case
    def video_exists_separates_membership_from_detail():
        with support.TempCorpus() as conn:
            video_id = support.seed(conn)
            assert store.video_exists(conn, video_id)
            assert not store.video_exists(conn, ABSENT_ID)


def _endpoint_cases() -> int:
    """Register and run the endpoint cases inside one live corpus and server.

    Registration and execution cannot be split across two calls here: the cases
    close over `origin`, so running them after this function returned would mean
    posting to a server that has already been shut down.
    """
    suite = support.Suite()

    with support.TempCorpus() as conn:
        video_id = support.seed(conn)

        server = ThreadingHTTPServer((config.UI_HOST, 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://{config.UI_HOST}:{server.server_port}"

        def list_rows() -> list[dict]:
            with urllib.request.urlopen(f"{origin}/api/videos", timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))["rows"]

        try:
            @suite.case
            def post_marks_read():  # noqa: D401
                status, payload = _post(origin, "/api/read", {"video_id": video_id, "read": True})
                assert status == 200, (status, payload)
                assert payload["video_id"] == video_id
                assert payload["read"] is True
                assert payload["read_at"]
                assert list_rows()[0]["read_at"] == payload["read_at"]

            @suite.case
            def post_unmarks_read():
                status, payload = _post(origin, "/api/read", {"video_id": video_id, "read": False})
                assert status == 200, (status, payload)
                assert payload["read"] is False
                assert list_rows()[0]["read_at"] is None

            @suite.case
            def unknown_video_is_a_404():
                status, payload = _post(origin, "/api/read", {"video_id": ABSENT_ID, "read": True})
                assert status == 404, (status, payload)
                assert "no video" in payload["error"]

            @suite.case
            def malformed_video_id_is_a_400():
                for bad in ("", "not-an-id", "../../etc/passwd", 12, None, ["a"]):
                    status, _ = _post(origin, "/api/read", {"video_id": bad, "read": True})
                    assert status == 400, (bad, status)

            @suite.case
            def read_must_be_a_real_boolean():
                # Truthiness would silently mark a video unread for a client that
                # sent the string "false" meaning the opposite.
                for bad in ("false", "true", 0, 1, None, []):
                    status, _ = _post(origin, "/api/read", {"video_id": video_id, "read": bad})
                    assert status == 400, (bad, status)

            @suite.case
            def a_wrong_content_type_is_a_400():
                status, _ = _post(origin, "/api/read", raw=b"{}", content_type="text/plain")
                assert status == 400, status

            @suite.case
            def a_non_json_body_is_a_400():
                status, _ = _post(origin, "/api/read", raw=b"{nope")
                assert status == 400, status

            @suite.case
            def a_missing_body_is_a_400():
                status, _ = _post(origin, "/api/read", raw=b"")
                assert status == 400, status

            @suite.case
            def an_oversized_body_is_refused():
                # Well over _READ_BODY_LIMIT, and still short enough that the
                # refusal is the size check rather than the JSON parse.
                status, _ = _post(origin, "/api/read", raw=b"[" + b" " * 4096)
                assert status == 413, status

            @suite.case
            def bulk_read_marks_only_successful_transcripts_in_the_requested_scope():
                other_channel = support.seed(conn, video_id="vid00000002")
                conn.execute(
                    "UPDATE videos SET channel_id = 'other' WHERE video_id = ?",
                    (other_channel,),
                )
                pending = support.seed(
                    conn,
                    video_id="vid00000003",
                    status=store.PENDING,
                )

                status, payload = _post(origin, "/api/read/all", {"channel": "chan"})
                assert status == 200, (status, payload)
                assert payload["marked"] == 1
                assert payload["channel"] == "chan"
                rows = {row["video_id"]: row for row in list_rows()}
                assert rows[video_id]["read_at"]
                assert rows[other_channel]["read_at"] is None
                assert rows[pending]["read_at"] is None

                status, payload = _post(origin, "/api/read/all", {"channel": None})
                assert status == 200, (status, payload)
                assert payload["marked"] == 1
                assert {row["video_id"]: row for row in list_rows()}[other_channel]["read_at"]

                status, payload = _post(origin, "/api/read/all", {"channel": "chan"})
                assert status == 200, (status, payload)
                assert payload["marked"] == 0

            @suite.case
            def bulk_read_requires_an_explicit_valid_scope():
                for payload in ({}, {"channel": ""}, {"channel": 12}, {"channel": []}):
                    status, _ = _post(origin, "/api/read/all", payload)
                    assert status == 400, (payload, status)

            @suite.case
            def a_get_on_a_post_route_announces_post():
                request = urllib.request.Request(f"{origin}/api/read", method="GET")
                try:
                    urllib.request.urlopen(request, timeout=5)
                except urllib.error.HTTPError as error:
                    assert error.code == 405, error.code
                    assert error.headers.get("Allow") == "POST"
                else:
                    raise AssertionError("GET /api/read should not succeed")

            @suite.case
            def the_only_write_routes_are_allowlisted():
                # The narrowed guarantee: nothing else accepts a body, and the
                # other write verbs are all the same refusal.
                assert set(web._POST_ROUTES) == {
                    "/api/chat", "/api/read", "/api/read/all",
                }
                for verb in ("do_PUT", "do_PATCH", "do_DELETE"):
                    assert getattr(web.Handler, verb) is web.Handler._method_not_allowed

            # Inside the `try`, so the server is still listening when the cases
            # run. Returning after the `finally` would mean posting to a port that
            # has already been closed.
            status = suite.run()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        return status


def main() -> int:
    store_suite = support.Suite()
    _store_cases(store_suite)
    status = store_suite.run()
    return status | _endpoint_cases()


if __name__ == "__main__":
    raise SystemExit(main())
