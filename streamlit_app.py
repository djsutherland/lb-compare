from pathlib import Path
import pickle
import re

import arrow
from letterboxdpy.core.scraper import parse_url
from letterboxdpy.user import User
from letterboxdpy.pages.user_films import extract_movies_from_user_watched
import requests
import streamlit as st

CACHE_DIR: Path = Path(__file__).parent / "cache"


def grab_films(username: str):
    username = username.lower()

    CACHE_DIR.mkdir(exist_ok=True)
    cache_pth: Path = (CACHE_DIR / username).with_suffix(".pkl")
    if cache_pth.exists():
        with cache_pth.open("rb") as f:
            d = pickle.load(f)
        l, r = st.columns([0.2, 0.8])
        if not l.button("Re-scrape", key=f"reset_{username}"):
            r.write(
                f"Using saved films for {username} from {d['_loaded_at'].humanize()}."
            )
            return d

    text = f"Grabbing films for {username}"
    pbar = st.progress(0.0, text=text)

    user = User(username)
    page = user.pages.films
    first_dom = parse_url(f"{page.url}/page/1/")

    movies = extract_movies_from_user_watched(first_dom)

    pages = first_dom.find_all(class_="paginate-page")
    n_pages = int(pages[-1].text) if pages else 1
    pbar.progress(1 / n_pages, text=text)

    for i in range(2, n_pages + 1):
        dom = parse_url(f"{page.url}/page/{i}/")
        movies.update(extract_movies_from_user_watched(dom))
        pbar.progress(i / n_pages, text=text)

    movies["_loaded_at"] = arrow.utcnow()
    with open(cache_pth, "wb") as f:
        pickle.dump(movies, f)

    return movies


@st.cache_data
def get_username(string: str) -> str:
    if re.match(r"(?:https?://)?boxd.it/[a-zA-Z]+", string):
        resp = requests.head(string)
        if not resp.ok or "location" not in resp.headers:
            st.error("Confused by boxd.it; try just putting in the username itself.")
        else:
            string = resp.headers["location"]

    if m := re.match(r"(?:https?://)?letterboxd.com/([^/]+)", string):
        return m.group(1)
    elif "/" in string:
        st.error(f"The username `{string}` seems invalid; put in just the username.")
        return ""
    else:
        return string


st.title("🎬 lb-compare")
a, b, c, d, e = st.columns([0.25, 0.2, 0.2, 0.2, 0.1], vertical_alignment="bottom")
a.write("We'll look up movies that ")
un_from: str = get_username(b.text_input("letterboxd username", key="un_from"))
c.write("has watched, and ")
un_to: str = get_username(d.text_input("letterboxd username", key="un_to"))
e.write("hasn't.")

m_from = grab_films(un_from) if un_from else None
m_to = grab_films(un_to) if un_to else None

if m_from and m_to:
    cands = {k: v for k, v in m_from.items() if k not in m_to}
    st.write(f"{len(cands):,} candidate movies:")

    for k, d in sorted(
        cands.items(),
        key=lambda kd: ((d := kd[1])["rating"] or 0, d["liked"], -d["year"], d["name"]),
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
        st.write(
            f"{stars} {heart} [{d['name']}](https://letterboxd.com/film/{k}) ({d['year']})"
        )
