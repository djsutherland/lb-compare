from pathlib import Path
import pickle
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from letterboxdpy.core.scraper import Scraper
from streamlit.testing.v1 import AppTest

from test_flaresolverr_transport import api_response, page_response

SOURCE = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def films_html(slug, page_count=1):
    pagination = (
        f'<a class="paginate-page">{page_count}</a>' if page_count > 1 else ""
    )
    return f"""<html><title>Films • Letterboxd</title><body class="films-watched">
    <section class="col-main"><ul class="grid"><li class="griditem">
    <div class="react-component" data-film-id="1" data-film-slug="{slug}"
         data-item-name="Example (2000)"></div>
    <p class="poster-viewingdata"><span class="rating rated-8"></span></p>
    </li></ul>{pagination}</section></body></html>"""


class AppTests(unittest.TestCase):
    def setUp(self):
        self.original_session = Scraper._session
        self.addCleanup(Scraper.set_instance, self.original_session)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.script = self.root / "streamlit_app.py"
        shutil.copyfile(SOURCE, self.script)
        self.env = patch.dict("os.environ", {"LETTERBOXD_TRANSPORT": "flaresolverr"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = Mock()
        self.client.post.side_effect = self.post
        self.http_patch = patch("flaresolverr_transport.requests.Session",
                                return_value=self.client)
        self.http_patch.start()
        self.addCleanup(self.http_patch.stop)

    def post(self, endpoint, json, timeout):
        if json["cmd"] != "request.get":
            return api_response({"status": "ok"})
        url = json["url"]
        if "/watchlist/" in url:
            html = """<html><title>Watchlist • Letterboxd</title>
            <body class="watchlist"><div data-num-entries="0"></div></body></html>"""
        else:
            slug = "source-movie" if "/source/" in url else "other-movie"
            html = films_html(slug)
        return page_response(html, url=url)

    def run_app(self):
        app = AppTest.from_file(str(self.script))
        app.query_params["from"] = "source"
        app.query_params["to"] = "target"
        app.run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        return app

    def test_comparison_and_cached_rerun(self):
        app = self.run_app()
        self.assertFalse(app.error)
        self.assertIn("1 candidate movies:", [m.value for m in app.markdown])
        self.assertEqual(len(list((self.root / "cache").glob("*.pkl"))), 3)
        urls = [c.kwargs["json"]["url"] for c in self.client.post.call_args_list
                if c.kwargs["json"]["cmd"] == "request.get"]
        self.assertEqual(len(urls), 3)
        self.assertTrue(all("/films/" in url or "/watchlist/" in url for url in urls))
        self.client.reset_mock()
        app.run()
        self.assertFalse(app.exception)
        self.client.post.assert_not_called()

    def test_second_page_is_merged(self):
        original = self.post
        def post(endpoint, json, timeout):
            if json["cmd"] == "request.get" and "/source/films/" in json["url"]:
                second_page = "/page/2/" in json["url"]
                return page_response(films_html("second" if second_page else "first",
                                                1 if second_page else 2),
                                     url=json["url"])
            return original(endpoint, json, timeout)
        self.client.post.side_effect = post
        app = self.run_app()
        self.assertFalse(app.error)
        with (self.root / "cache/source-films.pkl").open("rb") as file:
            self.assertEqual(set(pickle.load(file)["films"]), {"first", "second"})

    def test_blocked_watchlist_is_not_cached(self):
        original = self.post
        def post(endpoint, json, timeout):
            if json["cmd"] == "request.get" and "/watchlist/" in json["url"]:
                return api_response({"status": "error",
                                     "message": "Cloudflare has blocked this request."}, 500)
            return original(endpoint, json, timeout)
        self.client.post.side_effect = post
        app = self.run_app()
        self.assertIn("blocked", app.error[0].value)
        self.assertFalse((self.root / "cache/target-watchlist.pkl").exists())
        self.assertEqual(self.client.post.call_args.kwargs["json"]["cmd"], "sessions.destroy")

    def test_private_watchlist_is_allowed(self):
        original = self.post
        def post(endpoint, json, timeout):
            if json["cmd"] == "request.get" and "/watchlist/" in json["url"]:
                return page_response(
                    "<html><title>Private • Letterboxd</title><body>"
                    '<section class="message">This watchlist is private.</section>'
                    "</body></html>", url=json["url"])
            return original(endpoint, json, timeout)
        self.client.post.side_effect = post
        app = self.run_app()
        self.assertFalse(app.error)
        self.assertTrue((self.root / "cache/target-watchlist.pkl").exists())

    def test_failed_later_page_does_not_write_partial_cache(self):
        def post(endpoint, json, timeout):
            if json["cmd"] != "request.get":
                return api_response({"status": "ok"})
            if "/page/2/" in json["url"]:
                return api_response({"status": "error", "message": "Solver timeout"}, 500)
            return page_response(films_html("first", 2), url=json["url"])
        self.client.post.side_effect = post
        app = self.run_app()
        self.assertIn("Solver timeout", app.error[0].value)
        self.assertFalse((self.root / "cache/source-films.pkl").exists())

    def test_missing_user_has_friendly_error(self):
        def post(endpoint, json, timeout):
            if json["cmd"] != "request.get":
                return api_response({"status": "ok"})
            return page_response("<html><title>Letterboxd - Not Found</title><body></body></html>",
                                 url=json["url"])
        self.client.post.side_effect = post
        app = self.run_app()
        self.assertIn("Can't find user source", app.error[0].value)
        self.assertFalse(list((self.root / "cache").glob("*.pkl")))


if __name__ == "__main__":
    unittest.main()
