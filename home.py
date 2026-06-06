import streamlit as st

# -----------------------------
# Page Config
# -----------------------------
st.set_page_config(
    page_title="Sports Odds Tools",
    page_icon="📊",
    layout="wide",
)

# -----------------------------
# Homepage
# -----------------------------
st.title("Sports Odds Tools")

st.write(
    """
    Welcome to your sports analysis dashboard.

    Use the sidebar to switch between tools.
    """
)

st.divider()

# -----------------------------
# Tool Cards
# -----------------------------
col1, col2 = st.columns(2)

with col1:
    st.subheader("⚾ MLB Matchup Tool")
    st.write(
        """
        Analyze MLB hitter vs pitcher matchups, view cached matchup tables,
        create bet entries, track history, and review payout performance.
        """
    )

with col2:
    st.subheader("🏀 NBA Analysis Tool")
    st.write(
        """
        Analyze NBA player props, matchup data, betting lines, and performance trends.
        """
    )

st.divider()

# -----------------------------
# Instructions
# -----------------------------


st.info("Select a tool from the sidebar to begin.")