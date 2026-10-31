"""Use FlareSolverr through letterboxdpy's existing session hook."""

from contextlib import contextmanager
from contextvars import ContextVar
from http import HTTPStatus
import logging
import os
import re
from urllib.parse import urlsplit
from uuid import uuid4

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from letterboxdpy.core.exceptions import (
    AccessDeniedError,
    InvalidResponseError,
    PageLoadError,
    PrivateRouteError,
    ResourceNotFoundError,
)
from letterboxdpy.core.scraper import Scraper
import requests

logger = logging.getLogger(__name__)
DEFAULT_URL = "http://localhost:8191/v1"


class FlareSolverrSession:
    def __init__(self, endpoint: str = DEFAULT_URL, max_timeout: int = 60_000):
        self.endpoint = endpoint.rstrip("/")
        if not self.endpoint.endswith("/v1"):
            self.endpoint += "/v1"
        self.max_timeout = max_timeout
        self._http = requests.Session()
        self._session_id = None

    def check_health(self):
        url = self.endpoint.rsplit("/", 1)[0] + "/health"
        try:
            response = self._http.get(url, timeout=(5, 10))
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PageLoadError(url, f"FlareSolverr health check failed: {exc}") from exc
        if not isinstance(data, dict) or data.get("status") != "ok":
            raise PageLoadError(url, "FlareSolverr health check did not return status 'ok'")

    def _command(self, payload: dict, url: str, *, cleanup: bool = False) -> dict:
        # Browser startup can take time in addition to the solver's timeout.
        read_timeout = 10 if cleanup else self.max_timeout / 1000 + 30
        try:
            response = self._http.post(
                self.endpoint, json=payload, timeout=(5, read_timeout)
            )
        except requests.RequestException as exc:
            raise PageLoadError(
                url, f"Could not reach FlareSolverr at {self.endpoint}: {exc}"
            ) from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise PageLoadError(url, "FlareSolverr returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise PageLoadError(url, "FlareSolverr returned an invalid response")
        if data.get("status") != "ok":
            message = str(data.get("message") or "Unknown solver error")
            if "cloudflare has blocked" in message.lower():
                raise AccessDeniedError(message)
            raise PageLoadError(url, f"FlareSolverr: {message}")
        if not response.ok:
            raise PageLoadError(url, f"FlareSolverr API returned HTTP {response.status_code}")
        return data

    def get(self, url: str, **_kwargs) -> requests.Response:
        # FlareSolverr owns the browser headers, user agent, and timeout.
        if self._session_id is None:
            self._session_id = f"lb-compare-{uuid4().hex}"
            self._command(
                {"cmd": "sessions.create", "session": self._session_id}, url
            )
        data = self._command(
            {
                "cmd": "request.get",
                "url": url,
                "session": self._session_id,
                "maxTimeout": self.max_timeout,
            },
            url,
        )
        solution = data.get("solution")
        if not isinstance(solution, dict):
            raise PageLoadError(url, "FlareSolverr response has no solution")
        html = solution.get("response")
        status = solution.get("status")
        final_url = solution.get("url")
        headers = solution.get("headers", {})
        if (
            not isinstance(html, str)
            or not html.strip()
            or type(status) is not int
            or not 100 <= status <= 599
            or not isinstance(final_url, str)
            or not final_url
            or not isinstance(headers, dict)
        ):
            raise PageLoadError(url, "FlareSolverr returned an incomplete page solution")

        # Use the target page's fields, not the API response's HTTP fields.
        response = requests.Response()
        response.status_code = status
        try:
            response.reason = HTTPStatus(status).phrase
        except ValueError:
            response.reason = "Unknown"
        response.url = final_url
        response.headers.update(headers)
        response.encoding = "utf-8"
        response._content = html.encode("utf-8")
        _check_letterboxd_page(url, response)
        return response

    def close(self):
        try:
            if self._session_id is not None:
                self._command(
                    {"cmd": "sessions.destroy", "session": self._session_id},
                    self.endpoint,
                    cleanup=True,
                )
        except (PageLoadError, AccessDeniedError):
            logger.warning("Could not destroy FlareSolverr session", exc_info=True)
        finally:
            self._session_id = None
            self._http.close()


def _check_letterboxd_page(url: str, response: requests.Response):
    """Check page errors even when the solver supplies a synthetic HTTP 200."""
    dom = BeautifulSoup(response.text, "lxml")
    title = dom.title.get_text(" ", strip=True).casefold() if dom.title else ""
    message = dom.select_one("section.message")
    message_text = message.get_text(" ", strip=True).casefold() if message else ""
    if (
        title in {"just a moment...", "ddos-guard"}
        or "cloudflare" in title
        or title.startswith("access denied")
        or dom.select_one("#cf-challenge-running, #challenge-spinner, #cf-error-details")
    ):
        raise AccessDeniedError(f"Letterboxd returned a challenge or block page: {url}")
    if (
        response.status_code == 404
        or title == "letterboxd - not found"
        or "can’t find the page" in message_text
        or "can't find the page" in message_text
    ):
        raise ResourceNotFoundError(url)

    path = urlsplit(url).path.strip("/").split("/")
    if len(path) > 1 and path[1] == "watchlist":
        # Limit this check to page content so scripts/menus cannot imply privacy.
        content = dom.select_one("#content") or dom.body or dom
        text = content.get_text(" ", strip=True).casefold()
        if "watchlist is private" in text or (message and "private" in message_text):
            raise PrivateRouteError(f"This watchlist is private: {url}")
    if response.status_code == 403:
        raise AccessDeniedError(f"Letterboxd denied access: {url}")
    if response.status_code != 200:
        raise InvalidResponseError(
            f"Letterboxd returned HTTP {response.status_code}: {url}",
            code=response.status_code,
        )
    if urlsplit(response.url).hostname not in {"letterboxd.com", "www.letterboxd.com"}:
        raise InvalidResponseError(f"Unexpected redirect from Letterboxd: {response.url}")
    if "letterboxd" not in title or message or not dom.body:
        raise InvalidResponseError(f"Unrecognized Letterboxd page: {url}")
    expected_class = None
    if len(path) == 1 and path[0]:
        expected_class = "screen-member-profile"
    elif len(path) > 1 and path[1] in {"films", "watchlist"}:
        expected_class = "films-watched" if path[1] == "films" else "watchlist"
    if expected_class and expected_class not in dom.body.get("class", []):
        raise InvalidResponseError(f"Unexpected Letterboxd page layout: {url}")


def validate_collection(dom, movies, obj_name: str):
    """Do not cache a parser failure as an empty collection."""
    if movies:
        return
    content = dom.select_one(".js-watchlist-main-content, .col-main")
    count = dom.select_one("[data-num-entries]")
    text = content.get_text(" ", strip=True).casefold() if content else ""
    has_posters = content and content.select_one(
        "[data-component-class='LazyPoster'], [data-film-id], .griditem, .posteritem"
    )
    is_empty = (count and count.get("data-num-entries") == "0") or re.search(
        r"no films|watchlist is empty|(?:hasn[’']t|haven[’']t|has not) (?:watched|added)",
        text,
    )
    if has_posters or not is_empty:
        raise InvalidResponseError(
            f"Could not read {obj_name} from Letterboxd; the page layout may have changed."
        )


_active_session = ContextVar("letterboxd_scraping_session", default=None)


class _SessionRouter:
    def get(self, url, **kwargs):
        session = _active_session.get()
        if session is None:
            raise PageLoadError(url, "Letterboxd request made outside a scraping session")
        return session.get(url, **kwargs)


_router = _SessionRouter()


@contextmanager
def scraping_session():
    """Reuse a browser for one comparison, isolated from other Streamlit runs."""
    transport = os.environ.get("LETTERBOXD_TRANSPORT", "flaresolverr").lower()
    if transport == "flaresolverr":
        session = FlareSolverrSession(os.environ.get("FLARESOLVERR_URL", DEFAULT_URL))
    elif transport == "direct":
        session = curl_requests.Session()
    else:
        raise PageLoadError(transport, "LETTERBOXD_TRANSPORT must be flaresolverr or direct")
    # Every rerun installs the same router. The actual session is context-local.
    Scraper.set_instance(_router)
    token = _active_session.set(session)
    try:
        yield
    finally:
        _active_session.reset(token)
        session.close()
