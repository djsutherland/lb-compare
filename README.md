# lb-compare

[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://lb-compare.streamlit.app/)

The app sends Letterboxd page requests through a local FlareSolverr server by
default. One browser session is reused for each comparison and closed afterward.
Existing cached results do not start a browser.

Set up FlareSolverr separately, then launch the app:

```sh
FLARESOLVERR_URL=http://localhost:8191/v1 ./run-on-maclinux.command
```

The launcher checks the server's `/health` endpoint, then fetches the Letterboxd
homepage through FlareSolverr once. It validates the returned status and HTML,
closes the test browser session, and starts Streamlit only if both checks pass.
A failure prints the reason and exits with a nonzero status.

Additional Streamlit arguments can be passed to the launcher, for example:

```sh
./run-on-maclinux.command --server.port 8502
```

The included `compose.yaml` is optional if you want to manage a local server
with `docker compose up -d`. The launcher does not start or manage FlareSolverr.
You can run only the startup checks with `uv run python check_startup.py`.
Running `uv run streamlit run streamlit_app.py` directly skips the startup checks.

Configuration environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLARESOLVERR_URL` | `http://localhost:8191/v1` | Server API URL; the base URL also works. |
| `LETTERBOXD_TRANSPORT` | `flaresolverr` | Set to `direct` to use letterboxdpy's original HTTP client and skip FlareSolverr startup checks. |

The server must be reachable from the machine running Streamlit. A hosted app
needs a separately reachable FlareSolverr service. FlareSolverr mode reports
server/solver failures without falling back to direct requests or caching failures
as empty watchlists. The `boxd.it` short-link lookup still uses a direct HEAD request.

The adapter reads the target HTTP status from `solution.status`. FlareSolverr
3.5.2 can supply 200 for a Letterboxd error page, so the returned HTML is checked
as well. The solver has a 60-second limit; the HTTP read timeout is 90 seconds.
The Docker configuration pins the tested server version, 3.5.2. The adapter was
tested with letterboxdpy 6.5.7.

Run the offline tests with:

```sh
uv run python -m unittest discover -s tests
```
