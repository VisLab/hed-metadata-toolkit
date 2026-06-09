"""
clients/test_pmc.py — Tests for clients/pmc.py.

PR-G focus (2026-05-30): the new ``fetch_image`` two-stage flow.
PMC restructured their image hosting in 2024 so the figure-bytes
fetch now goes through the article landing page on
``pmc.ncbi.nlm.nih.gov`` first, parses ``<img src>`` for CDN URLs,
then downloads from ``cdn.ncbi.nlm.nih.gov``.  Tests exercise both
stages via an injected fake session that serves responses in
order.

``lookup_by_pmcid`` is covered indirectly by
``acquire/test_acquire_markdown.py`` and the existing PR-B smoke
tests; no direct coverage gap to fill here.

No network: every test injects a fake ``Session`` (via the
``session=`` parameter on :func:`pmc.fetch_image` /
:func:`pmc._fetch_image_url_map`) whose ``.get`` returns
controllable responses.  Pattern matches ``acquire/test_fetch.py``
so test fixtures stay readable across the project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable
from unittest.mock import patch

import pytest
import requests

from hed_metadata_toolkit.clients import pmc as P  # noqa: E402, N812  module under test


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PMCID = "PMC4097944"
PMCID_DIGITS = "4097944"
GR1 = "fnhum-08-00443-g0001.jpg"
GR2 = "fnhum-08-00443-g0002.jpg"
CDN_GR1 = (
    f"https://cdn.ncbi.nlm.nih.gov/pmc/blobs/b194/{PMCID_DIGITS}/aa45e06da37a/{GR1}"
)
CDN_GR2 = (
    f"https://cdn.ncbi.nlm.nih.gov/pmc/blobs/b194/{PMCID_DIGITS}/4b981b33c3d2/{GR2}"
)

JPG_BYTES1 = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x01" * 64
JPG_BYTES2 = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x02" * 64


# ---------------------------------------------------------------------------
# Fake response + session
# ---------------------------------------------------------------------------


class FakeResp:
    """Minimal stand-in for a ``requests.Response``."""

    def __init__(
        self,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        stream_error: Exception | None = None,
        chunk_size: int = 65_536,
    ) -> None:
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self._stream_error = stream_error
        self._chunk_size = chunk_size
        self.closed = False

    def iter_content(self, chunk_size: int = 65_536) -> Iterable[bytes]:
        if self._stream_error is not None:
            raise self._stream_error
        if not self._body:
            return
        step = self._chunk_size if self._chunk_size else chunk_size
        for i in range(0, len(self._body), step):
            yield self._body[i : i + step]

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """Captures get-call args; serves queued responses in order.

    Unlike the existing acquire/test_fetch.py FakeSession (which
    re-serves a single response indefinitely), this one strictly
    pops — once the queue is empty, ``.get()`` raises.  PR-G tests
    rely on call-count being exact to verify the cache short-
    circuits the second landing-page fetch.
    """

    def __init__(
        self,
        responses: Iterable[FakeResp] | None = None,
        raise_exc: Exception | None = None,
        raise_exc_after: int | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._raise_exc = raise_exc
        self._raise_exc_after = raise_exc_after
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self._raise_exc is not None:
            if self._raise_exc_after is None or len(self.calls) > self._raise_exc_after:
                raise self._raise_exc
        if not self._responses:
            raise AssertionError(f"FakeSession.get called more than queued ({url})")
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear throttle state and the landing-page cache between tests.

    Without this, an earlier test's cache hit would let a later
    test's fetch silently skip the network and look misleadingly
    "successful".
    """
    P._last_call.clear()
    P.reset_image_url_cache()
    yield
    P._last_call.clear()
    P.reset_image_url_cache()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _landing_html(*figures: tuple[str, str], off_host: bool = False) -> bytes:
    """Build a minimal landing page HTML containing ``<img>`` tags.

    Each ``(filename, cdn_url)`` becomes an ``<img>`` tag at the
    given CDN URL.  ``off_host=True`` also injects an ``<img>`` from
    ``example.com`` so the host-filter test can confirm it's excluded.
    """
    figure_tags = "".join(f'<img alt="Fig" src="{url}"/>' for _name, url in figures)
    extra = (
        '<img alt="off-host" src="https://example.com/foo.png"/>' if off_host else ""
    )
    html = (
        "<html><head><title>Test</title></head><body>"
        + extra
        + figure_tags
        + "</body></html>"
    )
    return html.encode("utf-8")


def _landing_resp(
    *figures: tuple[str, str], status: int = 200, off_host: bool = False
) -> FakeResp:
    return FakeResp(
        status=status,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=(
            _landing_html(*figures, off_host=off_host)
            if status == 200
            else b"<html>err</html>"
        ),
    )


def _image_resp(
    body: bytes = JPG_BYTES1, status: int = 200, content_type: str = "image/jpeg"
) -> FakeResp:
    return FakeResp(
        status=status,
        headers={"Content-Type": content_type},
        body=body,
    )


# ---------------------------------------------------------------------------
# Landing-page URL map parser  (_fetch_image_url_map + _IMG_SRC_RE)
# ---------------------------------------------------------------------------


class TestFetchImageUrlMap:
    def test_parses_cdn_img_srcs(self) -> None:
        sess = FakeSession(responses=[_landing_resp((GR1, CDN_GR1), (GR2, CDN_GR2))])
        url_map = P._fetch_image_url_map(PMCID, session=sess)
        assert url_map == {GR1: CDN_GR1, GR2: CDN_GR2}

    def test_filters_out_non_cdn_hosts(self) -> None:
        # Landing page carries an off-host PNG alongside a cdn
        # figure.  Only the cdn figure should appear in the map.
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1), off_host=True),
            ]
        )
        url_map = P._fetch_image_url_map(PMCID, session=sess)
        assert url_map == {GR1: CDN_GR1}
        for url in url_map.values():
            assert url.startswith("https://cdn.ncbi.nlm.nih.gov/")

    def test_returns_empty_dict_when_no_figures(self) -> None:
        # 200 with HTML that has no matching <img> tags.
        sess = FakeSession(
            responses=[
                FakeResp(
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body=b"<html><body><p>nothing here</p></body></html>",
                )
            ]
        )
        url_map = P._fetch_image_url_map(PMCID, session=sess)
        assert url_map == {}

    def test_canonicalises_pmcid_in_landing_url(self) -> None:
        # Pass a non-canonical PMCID; verify the URL hits the
        # canonical form on the new pmc subdomain.
        sess = FakeSession(responses=[_landing_resp((GR1, CDN_GR1))])
        P._fetch_image_url_map(PMCID_DIGITS, session=sess)
        url = sess.calls[0]["url"]
        assert url == f"https://pmc.ncbi.nlm.nih.gov/articles/{PMCID}/"

    def test_invalid_pmcid_returns_none_without_request(self) -> None:
        sess = FakeSession()
        assert P._fetch_image_url_map("not-a-pmcid", session=sess) is None
        assert sess.calls == []

    def test_404_returns_none(self) -> None:
        sess = FakeSession(responses=[_landing_resp(status=404)])
        assert P._fetch_image_url_map(PMCID, session=sess) is None

    def test_500_returns_none(self) -> None:
        sess = FakeSession(responses=[_landing_resp(status=500)])
        assert P._fetch_image_url_map(PMCID, session=sess) is None

    def test_network_exception_returns_none(self) -> None:
        sess = FakeSession(raise_exc=requests.ConnectionError("DNS"))
        assert P._fetch_image_url_map(PMCID, session=sess) is None

    def test_oversize_returns_none(self) -> None:
        big = b"<html>" + b"\x00" * 6000 + b"</html>"
        sess = FakeSession(
            responses=[
                FakeResp(
                    status=200,
                    headers={"Content-Type": "text/html"},
                    body=big,
                    chunk_size=1024,
                )
            ]
        )
        url_map = P._fetch_image_url_map(PMCID, session=sess, max_bytes=2048)
        assert url_map is None

    def test_cache_hit_avoids_refetch(self) -> None:
        # First call populates the cache; second call must not hit
        # the network even if the queue is exhausted.
        sess = FakeSession(responses=[_landing_resp((GR1, CDN_GR1))])
        first = P._fetch_image_url_map(PMCID, session=sess)
        second = P._fetch_image_url_map(PMCID, session=sess)
        assert first == second == {GR1: CDN_GR1}
        assert len(sess.calls) == 1

    def test_transient_failure_is_not_cached(self) -> None:
        # First call fails with network error -> None, no cache entry.
        # Second call gets a fresh chance against a working queue.
        sess1 = FakeSession(raise_exc=requests.ConnectionError("DNS"))
        assert P._fetch_image_url_map(PMCID, session=sess1) is None
        sess2 = FakeSession(responses=[_landing_resp((GR1, CDN_GR1))])
        assert P._fetch_image_url_map(PMCID, session=sess2) == {GR1: CDN_GR1}


# ---------------------------------------------------------------------------
# Happy paths through fetch_image
# ---------------------------------------------------------------------------


class TestFetchImageSuccess:
    def test_two_stage_fetch_returns_bytes(self) -> None:
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1)),
                _image_resp(JPG_BYTES1),
            ]
        )
        r = P.fetch_image(PMCID, GR1, session=sess)
        assert r == JPG_BYTES1

    def test_image_url_comes_from_landing_page(self) -> None:
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1)),
                _image_resp(JPG_BYTES1),
            ]
        )
        P.fetch_image(PMCID, GR1, session=sess)
        # Call 1: landing page on new pmc subdomain.  Call 2: CDN.
        assert sess.calls[0]["url"] == f"https://pmc.ncbi.nlm.nih.gov/articles/{PMCID}/"
        assert sess.calls[1]["url"] == CDN_GR1

    def test_content_type_parameters_stripped_on_image(self) -> None:
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1)),
                FakeResp(
                    status=200,
                    headers={"Content-Type": "image/jpeg; charset=binary"},
                    body=JPG_BYTES1,
                ),
            ]
        )
        assert P.fetch_image(PMCID, GR1, session=sess) == JPG_BYTES1

    def test_second_figure_reuses_cached_landing(self) -> None:
        # First call: 2 HTTPs (landing + image).  Second call for a
        # different figure on the same PMCID: 1 HTTP (image only)
        # because the landing-page parse is cached.
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1), (GR2, CDN_GR2)),
                _image_resp(JPG_BYTES1),
                _image_resp(JPG_BYTES2),
            ]
        )
        assert P.fetch_image(PMCID, GR1, session=sess) == JPG_BYTES1
        assert P.fetch_image(PMCID, GR2, session=sess) == JPG_BYTES2
        # Three total calls: landing once + two images.
        assert len(sess.calls) == 3
        urls = [c["url"] for c in sess.calls]
        assert urls == [
            f"https://pmc.ncbi.nlm.nih.gov/articles/{PMCID}/",
            CDN_GR1,
            CDN_GR2,
        ]

    def test_canonicalises_pmcid_for_lookup(self) -> None:
        # Non-canonical input should still produce a successful
        # fetch — the landing URL gets canonicalised inside
        # _fetch_image_url_map.
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1)),
                _image_resp(JPG_BYTES1),
            ]
        )
        assert P.fetch_image("4097944", GR1, session=sess) == JPG_BYTES1


# ---------------------------------------------------------------------------
# Failure shapes — all return None
# ---------------------------------------------------------------------------


class TestFetchImageFailures:
    def test_invalid_pmcid_short_circuits_no_request(self) -> None:
        sess = FakeSession()
        assert P.fetch_image("not-a-pmcid", GR1, session=sess) is None
        assert sess.calls == []

    def test_empty_pmcid_short_circuits(self) -> None:
        sess = FakeSession()
        assert P.fetch_image("", GR1, session=sess) is None
        assert P.fetch_image("   ", GR1, session=sess) is None
        assert sess.calls == []

    def test_empty_filename_short_circuits(self) -> None:
        sess = FakeSession()
        assert P.fetch_image(PMCID, "", session=sess) is None
        assert P.fetch_image(PMCID, "   ", session=sess) is None
        assert sess.calls == []

    def test_non_string_filename_short_circuits(self) -> None:
        sess = FakeSession()
        assert P.fetch_image(PMCID, None, session=sess) is None  # type: ignore[arg-type]
        assert sess.calls == []

    def test_landing_404_returns_none(self) -> None:
        sess = FakeSession(responses=[_landing_resp(status=404)])
        assert P.fetch_image(PMCID, GR1, session=sess) is None

    def test_landing_network_error_returns_none(self) -> None:
        sess = FakeSession(raise_exc=requests.ConnectionError("DNS"))
        assert P.fetch_image(PMCID, GR1, session=sess) is None

    def test_filename_not_in_landing_returns_none(self) -> None:
        # Landing page parses fine but does NOT mention our filename.
        sess = FakeSession(responses=[_landing_resp((GR2, CDN_GR2))])
        assert P.fetch_image(PMCID, GR1, session=sess) is None
        # No second HTTP — we never tried a download.
        assert len(sess.calls) == 1

    def test_image_404_returns_none(self) -> None:
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1)),
                _image_resp(status=404, content_type="text/html", body=b"not found"),
            ]
        )
        assert P.fetch_image(PMCID, GR1, session=sess) is None

    def test_image_non_image_content_type_returns_none(self) -> None:
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1)),
                _image_resp(
                    status=200, content_type="text/html", body=b"<html>err</html>"
                ),
            ]
        )
        assert P.fetch_image(PMCID, GR1, session=sess) is None

    def test_image_octet_stream_returns_none(self) -> None:
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1)),
                _image_resp(
                    status=200, content_type="application/octet-stream", body=JPG_BYTES1
                ),
            ]
        )
        assert P.fetch_image(PMCID, GR1, session=sess) is None

    def test_image_oversize_returns_none(self) -> None:
        big = b"\x00" * 4096
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1)),
                FakeResp(
                    status=200,
                    headers={"Content-Type": "image/jpeg"},
                    body=big,
                    chunk_size=1024,
                ),
            ]
        )
        assert P.fetch_image(PMCID, GR1, session=sess, max_bytes=2048) is None

    def test_image_network_error_returns_none(self) -> None:
        # First call (landing) succeeds; second call (image) raises.
        # Use raise_exc_after=1 to let the first call through.
        sess = FakeSession(
            responses=[_landing_resp((GR1, CDN_GR1))],
            raise_exc=requests.ConnectionError("reset"),
            raise_exc_after=1,
        )
        assert P.fetch_image(PMCID, GR1, session=sess) is None

    def test_image_stream_exception_returns_none(self) -> None:
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1)),
                FakeResp(
                    status=200,
                    headers={"Content-Type": "image/jpeg"},
                    body=b"x",
                    stream_error=requests.ConnectionError("reset mid-stream"),
                ),
            ]
        )
        assert P.fetch_image(PMCID, GR1, session=sess) is None


# ---------------------------------------------------------------------------
# Throttle sharing with lookup_by_pmcid
# ---------------------------------------------------------------------------


class TestThrottleSharing:
    """``fetch_image`` shares the per-host throttle map with
    ``lookup_by_pmcid``.  PR-G's two-stage flow hits the same
    host twice (landing + image), so a cold call records the
    timestamp on each leg and a warm second call (cached landing)
    only records once for the image."""

    def test_records_host_timestamp(self) -> None:
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1)),
                _image_resp(JPG_BYTES1),
            ]
        )
        P.fetch_image(PMCID, GR1, session=sess)
        assert "ncbi.nlm.nih.gov" in P._last_call

    def test_back_to_back_image_calls_throttle(self) -> None:
        # Two fetch_image calls for the same PMCID.  Cache hits on
        # the second landing fetch, so only 3 throttle events total:
        # landing(t=10.0), image(t=10.1), image(t=10.34).
        sess = FakeSession(
            responses=[
                _landing_resp((GR1, CDN_GR1), (GR2, CDN_GR2)),
                _image_resp(JPG_BYTES1),
                _image_resp(JPG_BYTES2),
            ]
        )

        # monotonic() is called twice inside _throttle per call.
        # Three calls × 2 reads = 6 monotonic readings needed; the
        # first reading at t=10.0 sees gap=10.0 (no sleep, records
        # 10.0), second at 10.1 sees gap=0.1 (sleeps 0.24 to reach
        # 0.34), third at 10.34 sees gap=0.24 (sleeps 0.10).
        with (
            patch.object(P.time, "sleep") as fake_sleep,
            patch.object(
                P.time, "monotonic", side_effect=[10.0, 10.0, 10.1, 10.34, 10.34, 10.44]
            ),
        ):
            P.fetch_image(PMCID, GR1, session=sess)
            P.fetch_image(PMCID, GR2, session=sess)

        # Two sleeps (one per same-host follow-up call).
        assert fake_sleep.call_count == 2


# ---------------------------------------------------------------------------
# PR-H5 — PMC OA Web Service (lookup_oa_pdf_url + _normalise_oa_href)
# ---------------------------------------------------------------------------

# OA Web Service response shapes (XML).  Trimmed to the essentials —
# real responses include responseDate, request URL, etc.
OA_XML_PDF_OK = """\
<OA>
  <responseDate>2026-06-04 08:30:17</responseDate>
  <records>
    <record id="PMC4097944" license="CC BY">
      <link format="tgz" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/08/56/PMC4097944.tar.gz"/>
      <link format="pdf" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/08/56/fnhum-08-00443.pdf"/>
    </record>
  </records>
</OA>
"""

OA_XML_NOT_OA = """\
<OA>
  <responseDate>2026-06-04 08:30:17</responseDate>
  <error code="idIsNotOpenAccess">identifier 'PMC4598943' is not Open Access</error>
</OA>
"""

OA_XML_PDF_MISSING = """\
<OA>
  <responseDate>2026-06-04 08:30:17</responseDate>
  <records>
    <record id="PMC0000000">
      <link format="tgz" href="ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_package/01/02/PMC0000000.tar.gz"/>
    </record>
  </records>
</OA>
"""


class TestNormaliseOaHref:
    def test_ftp_scheme_promoted_to_https(self):
        assert (
            P._normalise_oa_href("ftp://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/x/y/z.pdf")
            == "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/x/y/z.pdf"
        )

    def test_https_passes_through(self):
        assert P._normalise_oa_href("https://x/y.pdf") == "https://x/y.pdf"

    def test_empty_input(self):
        assert P._normalise_oa_href("") == ""
        assert P._normalise_oa_href("   ") == ""


def _stub_requests_get(monkeypatch, *, text: str, status: int = 200):
    """Replace requests.get with a stub that records calls and returns
    a FakeResp-shaped object exposing the attributes lookup_oa_pdf_url
    relies on (status_code, text).
    """

    class _Resp:
        def __init__(self) -> None:
            self.status_code = status
            self.text = text

    calls: list[dict] = []

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Resp()

    monkeypatch.setattr(P.requests, "get", fake_get)
    return calls


class TestLookupOaPdfUrl:
    def test_oa_hit_returns_https_pdf_url(self, tmp_path: Path, monkeypatch):
        _stub_requests_get(monkeypatch, text=OA_XML_PDF_OK)
        out = P.lookup_oa_pdf_url("PMC4097944", tmp_path)
        assert out == (
            "https://ftp.ncbi.nlm.nih.gov/pub/pmc/oa_pdf/08/56/fnhum-08-00443.pdf"
        )

    def test_not_oa_returns_none(self, tmp_path: Path, monkeypatch):
        _stub_requests_get(monkeypatch, text=OA_XML_NOT_OA)
        assert P.lookup_oa_pdf_url("PMC4598943", tmp_path) is None

    def test_oa_response_without_pdf_link_returns_none(
        self, tmp_path: Path, monkeypatch
    ):
        _stub_requests_get(monkeypatch, text=OA_XML_PDF_MISSING)
        assert P.lookup_oa_pdf_url("PMC0000000", tmp_path) is None

    def test_invalid_pmcid_returns_none(self, tmp_path: Path, monkeypatch):
        calls = _stub_requests_get(monkeypatch, text=OA_XML_PDF_OK)
        assert P.lookup_oa_pdf_url("not-a-pmcid", tmp_path) is None
        # No network call should have been made.
        assert calls == []

    def test_normalises_bare_digit_pmcid(self, tmp_path: Path, monkeypatch):
        calls = _stub_requests_get(monkeypatch, text=OA_XML_PDF_OK)
        P.lookup_oa_pdf_url("4097944", tmp_path)
        assert "id=PMC4097944" in calls[0]["url"]

    def test_caches_response(self, tmp_path: Path, monkeypatch):
        # Second call with the same PMCID should hit the cache and
        # skip the network entirely.
        calls = _stub_requests_get(monkeypatch, text=OA_XML_PDF_OK)
        P.lookup_oa_pdf_url("PMC4097944", tmp_path)
        P.lookup_oa_pdf_url("PMC4097944", tmp_path)
        assert len(calls) == 1

    def test_network_error_returns_none_and_does_not_cache(
        self, tmp_path: Path, monkeypatch
    ):
        def fake_get(url, **kwargs):
            raise requests.ConnectionError("DNS failure")

        monkeypatch.setattr(P.requests, "get", fake_get)
        assert P.lookup_oa_pdf_url("PMC4097944", tmp_path) is None
        # Now swap to a working stub — the second call should hit
        # the network (no negative-cache).
        _stub_requests_get(monkeypatch, text=OA_XML_PDF_OK)
        out2 = P.lookup_oa_pdf_url("PMC4097944", tmp_path)
        assert out2 is not None
