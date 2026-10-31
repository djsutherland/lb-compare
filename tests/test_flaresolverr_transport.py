from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from threading import Barrier
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from letterboxdpy.core.exceptions import (
    AccessDeniedError,
    InvalidResponseError,
    PageLoadError,
    PrivateRouteError,
    ResourceNotFoundError,
)
from letterboxdpy.core.scraper import Scraper, parse_url
from letterboxdpy.pages.user_profile import parse_url as profile_parse_url
import requests

from flaresolverr_transport import scraping_session, validate_collection

PROFILE_URL = "https://letterboxd.com/example/"
WATCHLIST_URL = PROFILE_URL + "watchlist/page/1/"
PROFILE = """<html><head><title>Example’s profile • Letterboxd</title></head>
<body class="screen-member-profile"><p>Example</p></body></html>"""


def api_response(data, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(data).encode("utf-8")
    return response


def page_response(html=PROFILE, status=200, url=PROFILE_URL):
    return api_response({
        "status": "ok",
        "solution": {"status": status, "response": html, "url": url, "headers": {}},
    })


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.original_session = Scraper._session
        self.addCleanup(Scraper.set_instance, self.original_session)
        self.env = patch.dict("os.environ", {"LETTERBOXD_TRANSPORT": "flaresolverr",
                                          "FLARESOLVERR_URL": "http://solver:8191"})
        self.env.start()
        self.addCleanup(self.env.stop)

    @contextmanager
    def server(self, page):
        client = Mock()
        client.post.side_effect = lambda endpoint, json, timeout: (
            page if json["cmd"] == "request.get" else api_response({"status": "ok"})
        )
        with patch("flaresolverr_transport.requests.Session", return_value=client):
            yield client

    def test_hook_reuses_session_and_preserves_page_fields(self):
        page = page_response(url=PROFILE_URL + "?final=1")
        with self.server(page) as client, scraping_session():
            for fetch in (parse_url, profile_parse_url):
                dom = fetch(PROFILE_URL)
                self.assertEqual(dom.title.string, "Example’s profile • Letterboxd")
                self.assertEqual(dom.final_url, PROFILE_URL + "?final=1")
        payloads = [call.kwargs["json"] for call in client.post.call_args_list]
        self.assertEqual([p["cmd"] for p in payloads],
                         ["sessions.create", "request.get", "request.get", "sessions.destroy"])
        self.assertEqual(len({p["session"] for p in payloads}), 1)
        self.assertEqual(payloads[1]["maxTimeout"], 60_000)
        self.assertEqual(client.post.call_args_list[1].kwargs["timeout"], (5, 90))
        self.assertEqual(client.post.call_args_list[1].args[0], "http://solver:8191/v1")
        client.close.assert_called_once()

    def test_target_status_is_read_from_solution(self):
        with self.server(page_response(status=404)), scraping_session():
            with self.assertRaises(ResourceNotFoundError):
                parse_url(PROFILE_URL)

    def test_target_headers_are_case_insensitive(self):
        data = page_response().json()
        data["solution"]["headers"] = {"content-type": "text/html"}
        with self.server(api_response(data)), scraping_session():
            response = Scraper.instance().get(PROFILE_URL)
            self.assertEqual(response.headers["Content-Type"], "text/html")

    def test_missing_page_with_synthetic_200(self):
        html = "<html><title>Letterboxd - Not Found</title><body></body></html>"
        with self.server(page_response(html)), scraping_session():
            with self.assertRaises(ResourceNotFoundError):
                parse_url(PROFILE_URL)

    def test_private_watchlist_is_distinct_from_block(self):
        html = """<html><title>Private watchlist • Letterboxd</title><body>
        <section class="message">This member’s watchlist is private.</section></body></html>"""
        with self.server(page_response(html, 403, WATCHLIST_URL)), scraping_session():
            with self.assertRaises(PrivateRouteError):
                parse_url(WATCHLIST_URL)

    def test_challenge_is_not_an_empty_watchlist(self):
        html = "<html><title>Just a moment...</title><body></body></html>"
        with self.server(page_response(html, url=WATCHLIST_URL)) as client, scraping_session():
            with self.assertRaises(AccessDeniedError):
                parse_url(WATCHLIST_URL)
        self.assertEqual(sum(c.kwargs["json"]["cmd"] == "request.get"
                             for c in client.post.call_args_list), 1)

    def test_target_403_does_not_trigger_five_long_retries(self):
        with self.server(page_response(status=403)) as client, scraping_session():
            with self.assertRaises(AccessDeniedError):
                parse_url(PROFILE_URL)
        self.assertEqual(len(client.post.call_args_list), 3)

    def test_target_server_error(self):
        with self.server(page_response(status=503)), scraping_session():
            with self.assertRaises(InvalidResponseError) as error:
                parse_url(PROFILE_URL)
        self.assertEqual(error.exception.code, 503)

    def test_solver_timeout_does_not_retry_or_fall_back(self):
        page = api_response({"status": "error", "message": "Timeout after 60 seconds."}, 500)
        with self.server(page) as client, scraping_session():
            with self.assertRaisesRegex(PageLoadError, "Timeout"):
                parse_url(PROFILE_URL)
        self.assertEqual(len(client.post.call_args_list), 3)

    def test_solver_block_error_is_access_denied(self):
        page = api_response({"status": "error", "message": "Cloudflare has blocked this request."}, 500)
        with self.server(page), scraping_session():
            with self.assertRaises(AccessDeniedError):
                parse_url(PROFILE_URL)

    def test_unreachable_server_is_page_load_error(self):
        with self.server(page_response()) as client:
            client.post.side_effect = requests.ConnectionError("Connection refused")
            with self.assertLogs("flaresolverr_transport", level="WARNING"):
                with scraping_session(), self.assertRaisesRegex(PageLoadError, "Could not reach"):
                    parse_url(PROFILE_URL)
        client.close.assert_called_once()

    def test_malformed_solutions(self):
        for data in (
            [],
            {"status": "ok"},
            {"status": "ok", "solution": {}},
            {"status": "ok", "solution": {"response": PROFILE, "status": "200", "url": PROFILE_URL}},
            {"status": "ok", "solution": {"response": "", "status": 200, "url": PROFILE_URL}},
        ):
            with self.subTest(data=data), self.server(api_response(data)), scraping_session():
                with self.assertRaises(PageLoadError):
                    parse_url(PROFILE_URL)

    def test_invalid_json(self):
        page = requests.Response()
        page.status_code = 502
        page._content = b"Bad Gateway"
        with self.server(page), scraping_session():
            with self.assertRaisesRegex(PageLoadError, "invalid JSON"):
                parse_url(PROFILE_URL)

    def test_unexpected_page_and_redirect(self):
        pages = [
            page_response("<html><title>Sign in • Letterboxd</title><body></body></html>"),
            page_response(url="https://example.com/"),
        ]
        for page in pages:
            with self.subTest(page=page), self.server(page), scraping_session():
                with self.assertRaises(InvalidResponseError):
                    parse_url(PROFILE_URL)

    def test_cache_only_comparison_starts_no_browser(self):
        with self.server(page_response()) as client, scraping_session():
            pass
        client.post.assert_not_called()
        client.close.assert_called_once()

    def test_cleanup_runs_on_streamlit_style_stop(self):
        class Stop(BaseException):
            pass
        with self.server(page_response()) as client:
            with self.assertRaises(Stop), scraping_session():
                parse_url(PROFILE_URL)
                raise Stop()
        self.assertEqual(client.post.call_args.kwargs["json"]["cmd"], "sessions.destroy")
        with self.assertRaises(PageLoadError):
            parse_url(PROFILE_URL)

    def test_cleanup_failure_preserves_original_error(self):
        with self.server(page_response()) as client:
            def post(endpoint, json, timeout):
                if json["cmd"] == "sessions.destroy":
                    raise requests.ConnectionError("Stopped server")
                return page_response() if json["cmd"] == "request.get" else api_response({"status": "ok"})
            client.post.side_effect = post
            with self.assertLogs("flaresolverr_transport", level="WARNING"):
                with self.assertRaisesRegex(ValueError, "original"), scraping_session():
                    parse_url(PROFILE_URL)
                    raise ValueError("original")
        client.close.assert_called_once()

    def test_direct_mode_forwards_original_kwargs(self):
        with patch.dict("os.environ", {"LETTERBOXD_TRANSPORT": "direct"}):
            with patch("flaresolverr_transport.curl_requests.Session") as direct:
                direct.return_value.get.return_value = page_response()
                # Use a proper page response instead of a solver envelope.
                direct.return_value.get.return_value._content = PROFILE.encode()
                direct.return_value.get.return_value.url = PROFILE_URL
                with scraping_session():
                    parse_url(PROFILE_URL)
                self.assertEqual(direct.return_value.get.call_args.kwargs["impersonate"], "chrome")
                direct.return_value.close.assert_called_once()

    def test_invalid_transport_is_reported(self):
        with patch.dict("os.environ", {"LETTERBOXD_TRANSPORT": "typo"}):
            with self.assertRaises(PageLoadError), scraping_session():
                pass

    def test_concurrent_comparisons_keep_sessions_isolated(self):
        barrier = Barrier(2)
        def compare(index):
            with scraping_session():
                current = Scraper.instance()
                barrier.wait(timeout=5)
                current.get(PROFILE_URL)
        with self.server(page_response()) as client:
            # Distinct HTTP sessions make it possible to inspect routing.
            clients = [Mock(), Mock()]
            for item in clients:
                item.post.side_effect = client.post.side_effect
            with patch("flaresolverr_transport.requests.Session", side_effect=clients):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(compare, range(2)))
        session_ids = []
        for item in clients:
            payloads = [c.kwargs["json"] for c in item.post.call_args_list]
            self.assertEqual([p["cmd"] for p in payloads],
                             ["sessions.create", "request.get", "sessions.destroy"])
            self.assertEqual(len({p["session"] for p in payloads}), 1)
            session_ids.append(payloads[0]["session"])
        self.assertNotEqual(*session_ids)


class CollectionTests(unittest.TestCase):
    def test_explicit_empty_collections_are_allowed(self):
        for html in (
            '<div class="js-watchlist-content" data-num-entries="0"></div>',
            '<section class="col-main">No films watched.</section>',
            '<section class="col-main">This member hasn’t added any films.</section>',
        ):
            with self.subTest(html=html):
                validate_collection(BeautifulSoup(html, "lxml"), {}, "films")

    def test_unparsed_posters_and_unknown_empty_pages_are_rejected(self):
        for html in (
            '<section class="col-main"><li class="griditem"></li></section>',
            '<section class="col-main"></section>',
            '<html><body></body></html>',
            '<section class="col-main">No films<li class="griditem"></li></section>',
        ):
            with self.subTest(html=html), self.assertRaises(InvalidResponseError):
                validate_collection(BeautifulSoup(html, "lxml"), {}, "films")

    def test_nonempty_collections_are_allowed(self):
        validate_collection(BeautifulSoup("<html></html>", "lxml"), {"movie": {}}, "films")


if __name__ == "__main__":
    unittest.main()
