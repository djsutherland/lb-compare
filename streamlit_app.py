import streamlit as st
from letterboxdpy.user import User

st.title("🎬 lb-compare")
st.write("We'll look up movies that ")
un_from = st.text_input("letterboxd username", key="un_from")
st.write("has watched, and ")
un_to = st.text_input("letterboxd username", key="un_to")
st.write("hasn't.")

if un_from and un_to:
    st.write(f"Grabbing {un_from}'s watched movies...")
    u_from = User(un_from)
    m_from = u_from.get_films()['movies']

    st.write(f"Grabbing {un_to}'s watched movies...")
    u_to = User(un_to)
    m_to = u_to.get_films()['movies']

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