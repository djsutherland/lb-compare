import streamlit as st

from letterboxdpy.core.scraper import parse_url
from letterboxdpy.user import User
from letterboxdpy.pages.user_films import extract_movies_from_user_watched

def grab_films(username):
    text = f"Grabbing films for {username}"
    pbar = st.progress(0.0, text=text)

    user = User(username)
    page = user.pages.films
    first_dom = parse_url(f"{page.url}/page/1/")

    movies = extract_movies_from_user_watched(first_dom)

    pages = first_dom.find_all(class_='paginate-page')
    n_pages = int(pages[-1].text) if pages else 1
    pbar.progress(1 / n_pages, text=text)

    for i in range(2, n_pages + 1):
        dom = parse_url(f"{page.url}/page/{i}/")
        movies.update(extract_movies_from_user_watched(dom))
        pbar.progress(i / n_pages, text=text)
    
    return movies


st.title("🎬 lb-compare")
st.write("We'll look up movies that ")
un_from = st.text_input("letterboxd username", key="un_from")
st.write("has watched, and ")
un_to = st.text_input("letterboxd username", key="un_to")
st.write("hasn't.")

if un_from and un_to:
    m_from = grab_films(un_from)
    m_to = grab_films(un_to)

    cands = {k: v for k, v in m_from.items() if k not in m_to}
    st.write(f"{len(cands):,} candidate movies:")

    for k, d in sorted(cands.items(),
                       key=lambda kd: (kd[1]['rating'] or 0, kd[1]['liked']),
                       reverse=True):
        rating = d['rating'] or 0
        if rating == 0:
            stars = '&nbsp;' * 21
        else:
            stars = '★' * (rating // 2) + '½' * (rating % 2) + '☆' * ((10 - rating) // 2)
        heart = '❤️' if d['liked'] else '&nbsp;' * 5
        st.write(f"{stars} {heart} [{d['name']}](https://letterboxd.com/film/{k})")