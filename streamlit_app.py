from pathlib import Path
import pickle
import re

import arrow
from letterboxdpy.core.exceptions import InvalidResponseError
from letterboxdpy.core.scraper import parse_url
from letterboxdpy.user import User
from letterboxdpy.pages.user_films import extract_movies_from_user_watched
from letterboxdpy.utils.movies_extractor import extract_movies_from_vertical_list
import requests
import streamlit as st

CACHE_DIR: Path = Path(__file__).parent / "cache"


def _grab(username: str, obj_name: str, process_dom) -> dict:
    CACHE_DIR.mkdir(exist_ok=True)
    cache_pth: Path = (CACHE_DIR / f"{username}-{obj_name}").with_suffix(".pkl")
    if cache_pth.exists():
        with cache_pth.open("rb") as f:
            d = pickle.load(f)
        l, r = st.columns([0.2, 0.8])
        if not l.button("Re-scrape", key=f"reset_{obj_name}_{username}"):
            r.write(
                f"Using saved {obj_name} for {username} from {d['loaded_at'].humanize()}."
            )
            return d[obj_name]

    pbar_text = f"Grabbing {obj_name} for {username}"
    pbar = st.progress(0.0, text=pbar_text)

    try:
        user = User(username)
    except InvalidResponseError as e:
        if '"code": 404' in str(e):
            st.error(f"Can't find user {username}; typo?")
            return None
        else:
            raise

    page = getattr(user.pages, obj_name)
    first_dom = parse_url(f"{page.url}/page/1/")

    results = process_dom(first_dom)

    pages = first_dom.find_all(class_="paginate-page")
    n_pages = int(pages[-1].text) if pages else 1
    pbar.progress(1 / n_pages, text=pbar_text)

    for i in range(2, n_pages + 1):
        dom = parse_url(f"{page.url}/page/{i}/")
        results.update(process_dom(dom))
        pbar.progress(i / n_pages, text=pbar_text)

    res = {"loaded_at": arrow.utcnow(), obj_name: results}
    with open(cache_pth, "wb") as f:
        pickle.dump(res, f)
    return results


def grab_films(username: str):
    return _grab(
        username,
        "films",
        extract_movies_from_user_watched,
    )


def grab_watchlist(username: str):
    return _grab(
        username,
        "watchlist",
        lambda dom: {
            d["slug"] for d in extract_movies_from_vertical_list(dom).values()
        },
    )


@st.cache_data
def get_username(string: str) -> str:
    if re.match(r"(?:https?://)?boxd.it/[a-zA-Z]+", string):
        resp = requests.head(string)
        if not resp.ok or "location" not in resp.headers:
            st.error("Confused by boxd.it; try just putting in the username itself.")
        else:
            string = resp.headers["location"]

    if m := re.match(r"(?:https?://)?letterboxd.com/([^/]+)", string):
        return m.group(1).lower()
    elif "/" in string or "." in string:
        st.error(f"The username `{string}` seems invalid; put in just the username.")
        return ""
    else:
        return string.lower()


st.title("🎬 lb-compare")
a, b, c, d, e = st.columns([0.25, 0.2, 0.2, 0.2, 0.1], vertical_alignment="bottom")
a.write("We'll look up movies that ")
un_from = get_username(b.text_input("letterboxd username", key="un_from"))
c.write("has watched, and ")
un_to = get_username(d.text_input("letterboxd username", key="un_to"))
e.write("hasn't.")

if un_from and un_to:
    if un_from == un_to:
        st.error(f"{un_from} will have to sort this out on their own.")
        st.stop()

    m_from = grab_films(un_from)
    m_to = grab_films(un_to)
    watchlist_to = grab_watchlist(un_to)

    cands = {k: v for k, v in m_from.items() if k not in m_to}
    st.write(f"{len(cands):,} candidate movies:")

    for k, d in sorted(
        cands.items(),
        key=lambda kd: (
            (d := kd[1])["rating"] or 0,
            d["liked"],
            int(kd[0] in watchlist_to),
            -d["year"],
            d["name"],
        ),
        reverse=True,
    ):
        rating = d["rating"] or 0
        if rating == 0:
            stars = "&nbsp;" * 21
        else:
            stars = (
                "★" * (rating // 2) + "½" * (rating % 2) + "☆" * ((10 - rating) // 2)
            )
        heart = "❤️" if d["liked"] else "&nbsp;" * 5
        wlist = "📝" if k in watchlist_to else "&nbsp;" * 5
        st.write(
            f"{stars} {heart} {wlist} [{d['name']}](https://letterboxd.com/film/{k}) ({d['year']})"
        )
