"""Verify the separately managed FlareSolverr server before starting Streamlit."""

from contextlib import closing
import os
import sys

from letterboxdpy.core.exceptions import (
    AccessDeniedError,
    InvalidResponseError,
    PageLoadError,
    PrivateRouteError,
    ResourceNotFoundError,
)

from flaresolverr_transport import DEFAULT_URL, FlareSolverrSession

CHECK_URL = "https://letterboxd.com/davidehrlich/films/"


def main() -> int:
    transport = os.environ.get("LETTERBOXD_TRANSPORT", "flaresolverr").lower()
    if transport == "direct":
        print("Direct transport selected; skipping FlareSolverr startup checks.", flush=True)
        return 0
    if transport != "flaresolverr":
        print("LETTERBOXD_TRANSPORT must be flaresolverr or direct.", file=sys.stderr)
        return 1

    try:
        with closing(FlareSolverrSession(os.environ.get("FLARESOLVERR_URL", DEFAULT_URL))) as session:
            print(f"Checking FlareSolverr at {session.endpoint} ...", flush=True)
            session.check_health()
            print(f"Checking {CHECK_URL} through FlareSolverr ...", flush=True)
            session.get(CHECK_URL)
    except (
        AccessDeniedError,
        InvalidResponseError,
        PageLoadError,
        PrivateRouteError,
        ResourceNotFoundError,
    ) as exc:
        print(f"Startup check failed: {exc}", file=sys.stderr)
        return 1
    print("Startup checks passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
