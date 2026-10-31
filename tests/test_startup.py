from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import requests

from check_startup import CHECK_URL, main
from test_flaresolverr_transport import api_response, page_response

HOMEPAGE = """<html><title>Letterboxd • Your life in film</title>
<body class="screen-home"><h1>Letterboxd</h1></body></html>"""
ROOT = Path(__file__).resolve().parents[1]


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"LETTERBOXD_TRANSPORT": "flaresolverr",
                                          "FLARESOLVERR_URL": "http://solver:8191/v1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = Mock()
        self.client.get.return_value = api_response({"status": "ok"})
        self.client.post.side_effect = self.post
        self.http = patch("flaresolverr_transport.requests.Session", return_value=self.client)
        self.http.start()
        self.addCleanup(self.http.stop)

    def post(self, endpoint, json, timeout):
        if json["cmd"] == "request.get":
            return page_response(HOMEPAGE, url=CHECK_URL)
        return api_response({"status": "ok"})

    def run_check(self):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main()
        return status, stdout.getvalue(), stderr.getvalue()

    def test_healthy_server_fetches_letterboxd_once_and_cleans_up(self):
        status, stdout, stderr = self.run_check()
        self.assertEqual(status, 0)
        self.assertIn("Startup checks passed", stdout)
        self.assertEqual(stderr, "")
        self.client.get.assert_called_once_with("http://solver:8191/health", timeout=(5, 10))
        payloads = [call.kwargs["json"] for call in self.client.post.call_args_list]
        self.assertEqual([p["cmd"] for p in payloads],
                         ["sessions.create", "request.get", "sessions.destroy"])
        self.assertEqual(payloads[1]["url"], CHECK_URL)
        self.assertEqual(len({p["session"] for p in payloads}), 1)
        self.client.close.assert_called_once()

    def test_server_base_url_is_supported(self):
        with patch.dict("os.environ", {"FLARESOLVERR_URL": "http://solver:8191/"}):
            self.assertEqual(self.run_check()[0], 0)
        self.client.get.assert_called_once_with("http://solver:8191/health", timeout=(5, 10))
        self.assertEqual(self.client.post.call_args.args[0], "http://solver:8191/v1")

    def test_unreachable_server_stops_before_browser_request(self):
        self.client.get.side_effect = requests.ConnectionError("Connection refused")
        status, _, stderr = self.run_check()
        self.assertEqual(status, 1)
        self.assertIn("health check failed", stderr)
        self.client.post.assert_not_called()
        self.client.close.assert_called_once()

    def test_bad_health_status_and_json_are_rejected(self):
        for response in (
            api_response({"status": "error"}),
            api_response([]),
            api_response({"status": "ok"}, status=503),
        ):
            with self.subTest(response=response):
                self.client.get.return_value = response
                self.assertEqual(self.run_check()[0], 1)
                self.client.post.assert_not_called()
        self.client.get.return_value = Mock()
        self.client.get.return_value.json.side_effect = ValueError("Not JSON")
        self.assertEqual(self.run_check()[0], 1)
        self.client.post.assert_not_called()

    def test_letterboxd_challenge_stops_startup_and_closes_browser(self):
        def post(endpoint, json, timeout):
            if json["cmd"] == "request.get":
                return page_response(
                    "<html><title>Just a moment...</title><body></body></html>",
                    url=CHECK_URL,
                )
            return self.post(endpoint, json, timeout)
        self.client.post.side_effect = post
        status, _, stderr = self.run_check()
        self.assertEqual(status, 1)
        self.assertIn("challenge or block page", stderr)
        self.assertEqual(self.client.post.call_args.kwargs["json"]["cmd"], "sessions.destroy")

    def test_solver_failure_stops_startup(self):
        def post(endpoint, json, timeout):
            if json["cmd"] == "request.get":
                return api_response({"status": "error", "message": "Timeout"}, status=500)
            return self.post(endpoint, json, timeout)
        self.client.post.side_effect = post
        status, _, stderr = self.run_check()
        self.assertEqual(status, 1)
        self.assertIn("Timeout", stderr)
        self.assertEqual(self.client.post.call_args.kwargs["json"]["cmd"], "sessions.destroy")

    def test_direct_mode_skips_flaresolverr(self):
        with patch.dict("os.environ", {"LETTERBOXD_TRANSPORT": "direct"}):
            status, stdout, _ = self.run_check()
        self.assertEqual(status, 0)
        self.assertIn("skipping", stdout)
        self.client.get.assert_not_called()
        self.client.post.assert_not_called()

    def test_invalid_transport_fails_without_network(self):
        with patch.dict("os.environ", {"LETTERBOXD_TRANSPORT": "typo"}):
            self.assertEqual(self.run_check()[0], 1)
        self.client.get.assert_not_called()
        self.client.post.assert_not_called()


class LauncherTests(unittest.TestCase):
    def run_launcher(self, check_status):
        with TemporaryDirectory() as folder:
            folder = Path(folder)
            log = folder / "calls"
            uv = folder / "uv"
            uv.write_text("""#!/bin/sh
echo "$*" >> "$TEST_UV_LOG"
if [ "$1" = run ] && [ "$2" = python ]; then
  exit "$TEST_CHECK_STATUS"
fi
exit 0
""")
            uv.chmod(0o755)
            result = subprocess.run(
                ["/bin/sh", str(ROOT / "run-on-maclinux.command"), "--server.port", "8502"],
                env={**os.environ, "PATH": str(folder) + os.pathsep + os.environ["PATH"],
                     "TEST_UV_LOG": str(log), "TEST_CHECK_STATUS": str(check_status)},
                capture_output=True, text=True, check=False,
            )
            return result, log.read_text().splitlines()

    def test_failed_check_prevents_streamlit_start(self):
        result, calls = self.run_launcher(23)
        self.assertEqual(result.returncode, 23)
        self.assertEqual(calls, ["run python check_startup.py"])

    def test_successful_check_starts_streamlit_and_forwards_arguments(self):
        result, calls = self.run_launcher(0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls[0], "run python check_startup.py")
        self.assertEqual(calls[1], "run streamlit run streamlit_app.py "
                         "--server.address localhost --browser.gatherUsageStats false "
                         "--server.port 8502")


if __name__ == "__main__":
    unittest.main()
