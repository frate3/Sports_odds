import warnings
from datetime import datetime
from math import comb

import pandas as pd
import requests
import streamlit as st
from pybaseball import cache
import os

import MLB.calc as calc
import MLB.probable as probable
from MLB.bet_handle import create_entry, fill_blanks, search_db, calc_payout, write_to_db
from MLB.kalshi_handle import get_kalshi_hit_odds
from MLB.time_handle import get_game_start_times

warnings.filterwarnings("ignore", category=FutureWarning)
cache.enable()

def build_payout_summary():
    """Build day-by-day profit totals for the payout view."""
    rows = search_db(only_open=False)

    if not rows:
        return pd.DataFrame(), pd.DataFrame()

    df = pd.DataFrame(rows)

    if df.empty or "Payout" not in df.columns or "Wager" not in df.columns:
        return df, pd.DataFrame()

    df["Payout"] = pd.to_numeric(df["Payout"], errors="coerce")
    df["Wager"] = pd.to_numeric(df["Wager"], errors="coerce")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    settled_df = df.dropna(subset=["Date", "Payout", "Wager"]).copy()

    if settled_df.empty:
        return df, pd.DataFrame()

    settled_df["Profit"] = settled_df["Payout"] - settled_df["Wager"]

    daily_profit = (
        settled_df.groupby(settled_df["Date"].dt.date)["Profit"]
        .sum()
        .reset_index(name="Daily Profit")
    )

    daily_profit["Date"] = pd.to_datetime(daily_profit["Date"])
    daily_profit = daily_profit.sort_values("Date")
    daily_profit["Net Before Day"] = (
        daily_profit["Daily Profit"].cumsum().shift(1).fillna(0)
    )
    daily_profit["Net After Day"] = daily_profit["Daily Profit"].cumsum()

    return df, daily_profit

def load_cached_matchup(hitter_team_name, pitcher_name):
    today = datetime.now().strftime("%Y-%m-%d")

    cache_dir = "data/matchup_cache"

    if not os.path.exists(cache_dir):
        return None

    hitter_team_key = hitter_team_name.replace(" ", "_")
    pitcher_key = pitcher_name.replace(" ", "_")

    for filename in os.listdir(cache_dir):
        if not filename.endswith(".csv"):
            continue

        if not filename.startswith(today):
            continue

        if hitter_team_key in filename and pitcher_key in filename:
            path = os.path.join(cache_dir, filename)
            return pd.read_csv(path)

    return None

def american_to_probability(odds):
    odds = int(odds)
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)

def probability_to_american(prob):
    if prob is None or prob <= 0 or prob >= 1:
        return None
    if prob >= 0.5:
        return f"-{round(100 * prob / (1 - prob))}"
    return f"+{round(100 * (1 - prob) / prob)}"

def format_american(odds):
    if odds is None or odds == "N/A":
        return "N/A"
    odds = int(odds)
    return f"+{odds}" if odds > 0 else str(odds)

def normalize_hit_odds(raw_odds):
    """
    Accepts either:
      - your new function returning 3 values: 0-hit odds, 1-hit odds, 2-hit odds
      - a dict with 0/1/2 hit keys
      - the old Kalshi dataframe with rows for 1+ hits and 2+ hits

    Returns:
      {"0 hits": odds, "1+ hits": odds, "2+ hits": odds}
    """
    if raw_odds is None:
        return {}

    if isinstance(raw_odds, (list, tuple)) and len(raw_odds) == 3:
        return {
            "0 hits": format_american(raw_odds[0]),
            "1+ hits": format_american(raw_odds[1]),
            "2+ hits": format_american(raw_odds[2]),
        }

    if isinstance(raw_odds, dict):
        return {
            "0 hits": format_american(raw_odds.get("0 hits") or raw_odds.get("0_hit") or raw_odds.get("0")),
            "1+ hits": format_american(raw_odds.get("1+ hits") or raw_odds.get("1_hit") or raw_odds.get("1")),
            "2+ hits": format_american(raw_odds.get("2+ hits") or raw_odds.get("2_hits") or raw_odds.get("2")),
        }

    if isinstance(raw_odds, pd.DataFrame):
        odds = {}

        for _, row in raw_odds.iterrows():
            line = str(row.get("line", "")).lower()
            american = row.get("american_odds")

            if "1" in line:
                odds["1+ hits"] = format_american(american)
            elif "2" in line:
                odds["2+ hits"] = format_american(american)

        # 0 hits is the inverse of 1+ hits.
        if odds.get("1+ hits") not in (None, "N/A"):
            p_1_plus = american_to_probability(odds["1+ hits"])
            odds["0 hits"] = probability_to_american(1 - p_1_plus)

        return odds

    return {}

def get_kalshi_hit_percents(player_name):
    """
    Returns Kalshi implied percentages for 1+ hits and 2+ hits.
    """
    try:
        kalshi_df = get_kalshi_hit_odds(player_name)
        
        if kalshi_df is None or kalshi_df.empty:
            return "N/A", "N/A"
            kalshi_df = get_kalshi_hit_odds(player_name)
    
        if kalshi_df is None or kalshi_df.empty:
            print( "N/A", "N/A")

        one_hit_row = kalshi_df[1]
        two_hit_row = kalshi_df[2]
        
        kalshi_1 = f"{round(american_to_probability(one_hit_row),2)*100}%"
        kalshi_2 = f"{round(american_to_probability(two_hit_row),2)*100}%"

        return kalshi_1, kalshi_2

    except Exception:
        return "N/A", "N/A"

# -----------------------------
# Streamlit UI
# -----------------------------
st.set_page_config(page_title="MLB Matchup Tool", layout="wide")

st.title("MLB Matchup Tool")

st.markdown("""
<style>
.game-card-title {
    font-size: 0.9rem;
    font-weight: 700;
    margin-bottom: 0.35rem;
}

.game-team-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 1.05rem;
    font-weight: 700;
    padding: 0.15rem 0;
}

.game-selected {
    font-size: 0.8rem;
    font-weight: 700;
    color: #77dd77;
}

.selected-matchup-box {
    border: 1px solid #444;
    border-radius: 8px;
    padding: 0.75rem;
    margin-top: 0.75rem;
    background-color: #1f1f1f;
}
</style>
""", unsafe_allow_html=True)


def select_probable_matchup(game, batting_side):
    """
    batting_side:
        'away' means away team hitters vs home probable pitcher
        'home' means home team hitters vs away probable pitcher
    """

    if batting_side == "away":
        hitter_team_name = game["away_team"]
        hitter_team_id = game["away_team_id"]
        pitcher_name = game["home_pitcher_name"]
        pitcher_id = game["home_pitcher_id"]
    else:
        hitter_team_name = game["home_team"]
        hitter_team_id = game["home_team_id"]
        pitcher_name = game["away_pitcher_name"]
        pitcher_id = game["away_pitcher_id"]

    st.session_state["selected_batting_side"] = batting_side
    st.session_state["selected_game_key"] = f"{game['away_team']}@{game['home_team']}"

    st.session_state["team2_name"] = hitter_team_name
    st.session_state["team2_id"] = hitter_team_id
    st.session_state["pitcher_name"] = pitcher_name
    st.session_state["pitcher_id"] = pitcher_id

    # Clear old table/splits when switching games.
    for key in [
        "matchup_df",
        "selected_hitter_name",
        "selected_hitter_id",
        "vs_right",
        "vs_left",
    ]:
        if key in st.session_state:
            del st.session_state[key]


st.subheader("Today's Probable Matchups")

try:
    today_games = probable.get_today_probable_games()
except Exception as exc:
    today_games = []
    st.error(f"Could not load today's probable games: {exc}")

if not today_games:
    st.warning("No probable games found for today.")

else:
    games_per_page = 6
    times_df = get_game_start_times()

    if "games_page" not in st.session_state:
        st.session_state["games_page"] = 0

    total_pages = max(1, (len(today_games) + games_per_page - 1) // games_per_page)

    nav_col1, nav_col2, nav_col3 = st.columns([1, 3, 1])

    with nav_col1:
        if st.button("← Previous", disabled=st.session_state["games_page"] == 0):
            st.session_state["games_page"] -= 1
            st.rerun()

    with nav_col2:
        st.markdown(
            f"<div style='text-align:center; font-weight:700;'>"
            f"Games {st.session_state['games_page'] + 1} of {total_pages}"
            f"</div>",
            unsafe_allow_html=True,
        )

    with nav_col3:
        if st.button(
            "Next →",
            disabled=st.session_state["games_page"] >= total_pages - 1,
        ):
            st.session_state["games_page"] += 1
            st.rerun()

    start = st.session_state["games_page"] * games_per_page
    end = start + games_per_page
    row_games = today_games[start:end]

    game_cols = st.columns(games_per_page)

    for i, game in enumerate(row_games):
        game_index = start + i
        game_key = f"{game['away_team']}@{game['home_team']}"

        away_selected = (
            st.session_state.get("selected_game_key") == game_key
            and st.session_state.get("selected_batting_side") == "away"
        )

        home_selected = (
            st.session_state.get("selected_game_key") == game_key
            and st.session_state.get("selected_batting_side") == "home"
        )

        with game_cols[i]:
            with st.container(border=True):

                st.markdown(
                    f"""
                    <div class="game-card-title">{times_df["start_time"][i+start]} - {times_df["status"][i+start]}</div>
                    """,
                    unsafe_allow_html=True,
                )

                away_disabled = game["home_pitcher_id"] is None
                home_disabled = game["away_pitcher_id"] is None

                away_label = f"{game.get('away_abbr', game['away_team'][:3].upper())} batting"
                home_label = f"{game.get('home_abbr', game['home_team'][:3].upper())} batting"

                if away_selected:
                    st.markdown("<div class='game-selected'>Selected</div>", unsafe_allow_html=True)

                if st.button(
                    away_label,
                    key=f"select_away_batting_{game_index}",
                    width="stretch",
                    disabled=away_disabled,
                ):
                    select_probable_matchup(game, "away")
                    st.rerun()

                if home_selected:
                    st.markdown("<div class='game-selected'>Selected</div>", unsafe_allow_html=True)

                if st.button(
                    home_label,
                    key=f"select_home_batting_{game_index}",
                    width="stretch",
                    disabled=home_disabled,
                ):
                    select_probable_matchup(game, "home")
                    st.rerun()

team2_name = st.session_state.get("team2_name")
team2_id = st.session_state.get("team2_id")
pitcher_name = st.session_state.get("pitcher_name")
pitcher_id = st.session_state.get("pitcher_id")

if team2_name and pitcher_name:
    st.markdown(
        f"""
        <div class="selected-matchup-box">
            <b>Viewing:</b> {team2_name} batters vs {pitcher_name}
        </div>
        """,
        unsafe_allow_html=True,
    )

hitters = calc.get_hitters(team2_id) if team2_id else {}

st.divider()

btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([1, 1, 1, 1])

with btn_col1:
    run_matchup_clicked = st.button("Run Matchup", type="primary")

with btn_col2:
    create_line_clicked = st.button("Create Line", type="primary")

with btn_col3:
    view_history_clicked = st.button("View History", type="primary")

with btn_col4:
    payout_clicked = st.button("Payout", type="primary")

if view_history_clicked:
    st.session_state["show_history"] = not st.session_state.get("show_history", False)

if payout_clicked:
    st.session_state["show_payout"] = not st.session_state.get("show_payout", False)

if run_matchup_clicked:
    if pitcher_id is None or team2_id is None:
        st.error("Select a game and batting team first.")

    else:
        cached_df = load_cached_matchup(team2_name, pitcher_name)

        if cached_df is not None:
            st.success("Loaded today's precomputed matchup table.")

            kalshi_1_values = []
            kalshi_2_values = []

            progress = st.progress(0, text="Fetching fresh Kalshi odds...")

            for i, hitter_name in enumerate(cached_df["Hitter Name"], start=1):
                kalshi_1_hit, kalshi_2_hits = get_kalshi_hit_percents(hitter_name)

                kalshi_1_values.append(kalshi_1_hit)
                kalshi_2_values.append(kalshi_2_hits)

                progress.progress(
                    i / len(cached_df),
                    text=f"Fetching fresh Kalshi odds... {i}/{len(cached_df)}",
                )

            cached_df["Kalshi 1 hit %"] = kalshi_1_values
            cached_df["Kalshi 2 hit %"] = kalshi_2_values

            st.session_state["matchup_df"] = cached_df
            st.session_state["pitcher_id"] = pitcher_id
            st.session_state["pitcher_name"] = pitcher_name
            st.session_state["team2_name"] = team2_name

            # Optional: only needed if you want the split view to work immediately.
            with st.spinner("Loading pitcher split data for detail view..."):
                vs_right, vs_left = calc.get_pitch_data(pitcher_id)

            st.session_state["vs_right"] = vs_right
            st.session_state["vs_left"] = vs_left

        else:
            st.warning("No cached matchup found. Running live calculation.")

            with st.spinner("Fetching pitcher data, league averages, and hitter projections..."):
                vs_right, vs_left = calc.get_pitch_data(pitcher_id)

                league_avgs = calc.get_league_averages()
                lg_right = league_avgs.get("R", {})
                lg_left = league_avgs.get("L", {})

                pitcher_k_rate, league_k_rate, pitcher_k_ratio = calc.get_pitcher_k_ratio(
                    pitcher_id
                )

            if vs_right is None and vs_left is None:
                st.error("No Statcast data found for this pitcher this season.")

            else:
                rows = []

                progress = st.progress(0)
                hitter_items = list(hitters.items())

                for i, (hitter_name, hitter_id) in enumerate(hitter_items, start=1):
                    try:
                        row = calc.build_hitter_row(
                            hitter_name=hitter_name,
                            hitter_id=hitter_id,
                            pitcher_id=pitcher_id,
                            vs_right=vs_right,
                            vs_left=vs_left,
                            lg_right=lg_right,
                            lg_left=lg_left,
                            pitcher_k_ratio=pitcher_k_ratio,
                        )

                        kalshi_1_hit, kalshi_2_hits = get_kalshi_hit_percents(hitter_name)

                        row["Kalshi 1 hit %"] = kalshi_1_hit
                        row["Kalshi 2 hit %"] = kalshi_2_hits
                        row["Hitter ID"] = hitter_id

                    except Exception as exc:
                        row = {
                            "Hitter Name": hitter_name,
                            "abs": "Error",
                            "Prob 1 hit": "Error",
                            "Kalshi 1 hit %": "Error",
                            "Prob 2 hits": "Error",
                            "Kalshi 2 hit %": "Error",
                            "Hitter ID": hitter_id,
                        }

                        st.caption(f"Skipped {hitter_name}: {exc}")

                    rows.append(row)
                    progress.progress(i / len(hitter_items))

                df = pd.DataFrame(
                    rows,
                    columns=[
                        "Hitter Name",
                        "abs",
                        "Prob 1 hit",
                        "Kalshi 1 hit %",
                        "Prob 2 hits",
                        "Kalshi 2 hit %",
                        "Hitter ID",
                    ],
                )

                st.session_state["matchup_df"] = df
                st.session_state["pitcher_id"] = pitcher_id
                st.session_state["pitcher_name"] = pitcher_name
                st.session_state["team2_name"] = team2_name
                st.session_state["vs_right"] = vs_right
                st.session_state["vs_left"] = vs_left


# -----------------------------
# View / Manually Settle History
# -----------------------------

if st.session_state.get("show_payout"):
    st.divider()
    st.subheader("Payout Summary")

    try:
        _, daily_profit = build_payout_summary()

        if daily_profit.empty:
            st.info("No settled bets with payouts yet.")
        else:
            total_profit = daily_profit["Daily Profit"].sum()
            last_net = daily_profit["Net After Day"].iloc[-1]
            best_day = daily_profit["Daily Profit"].max()
            worst_day = daily_profit["Daily Profit"].min()

            metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
            metric_col1.metric("Total Profit", f"${total_profit:.2f}")
            metric_col2.metric("Current Net", f"${last_net:.2f}")
            metric_col3.metric("Best Day", f"${best_day:.2f}")
            metric_col4.metric("Worst Day", f"${worst_day:.2f}")

            chart_df = daily_profit.set_index("Date")[[
                "Daily Profit",
                "Net Before Day",
                "Net After Day",
            ]]

            st.line_chart(chart_df, width="stretch")

            table_df = daily_profit.copy()
            table_df["Date"] = table_df["Date"].dt.strftime("%Y-%m-%d")

            st.dataframe(
                table_df,
                width="stretch",
                hide_index=True,
            )

    except Exception as exc:
        st.error(f"Could not load payout summary: {exc}")

if st.session_state.get("show_history"):
    st.divider()
    st.subheader("Unsettled Bet History")

    auto_col, refresh_col = st.columns([1, 1])

    with auto_col:
        if st.button("Try Auto Fill Results"):
            try:
                fill_blanks()
                st.success("Auto fill completed.")
                st.rerun()
            except Exception as exc:
                st.error(f"Auto fill failed: {exc}")

    unsettled_rows = search_db()

    if not unsettled_rows:
        st.info("No unsettled bets.")
    else:
        history_df = pd.DataFrame(unsettled_rows)

        st.dataframe(
            history_df,
            width="stretch",
            hide_index=True,
        )

        st.markdown("### Manually Settle Result")

        row_labels = [
            f"{row['ID']} | {row['Date']} | {row['Name']} | Line: {row['Line']} | Odds: {row['Odds']} | Wager: {row['Wager']}"
            for row in unsettled_rows
        ]

        selected_label = st.selectbox(
            "Select bet to settle",
            row_labels,
            key="manual_settle_row",
        )

        selected_id = int(selected_label.split("|")[0].strip())

        selected_row = next(
            row for row in unsettled_rows
            if int(row["ID"]) == selected_id
        )

        input_col1, input_col2 = st.columns(2)

        with input_col1:
            manual_hits = st.number_input(
                "Hits",
                min_value=0,
                max_value=10,
                step=1,
                key="manual_hits",
            )

        with input_col2:
            manual_abs = st.number_input(
                "At Bats",
                min_value=0,
                max_value=10,
                step=1,
                key="manual_abs",
            )

        manual_result = f"{manual_hits}/{manual_abs}"

        try:
            line_value = float(selected_row["Line"])
            wager_value = float(selected_row["Wager"])

            
            if manual_hits > line_value and line_value != 0:
                manual_payout = round(calc_payout(selected_row["Odds"], wager_value), 2)
                print("here")
            elif line_value==0 and manual_hits==0:
                manual_payout = round(calc_payout(selected_row["Odds"], wager_value), 2)
            else:
                manual_payout = 0

            st.caption(
                f"Result will be saved as **{manual_result}** with payout **{manual_payout:.2f}**"
            )

            if st.button("Save Manual Result", type="primary"):
                write_to_db(
                    selected_row["ID"],
                    manual_result,
                    manual_payout,
                )

                st.success(
                    f"Saved result for {selected_row['Name']}: {manual_result}"
                )
                st.rerun()

        except Exception as exc:
            # st.error(f"Could not calculate manual payout: {exc}")
            print(" ")

# Show results table if a matchup has been run
if "matchup_df" in st.session_state:
    df = st.session_state["matchup_df"]

    st.subheader(
        f"{st.session_state['team2_name']} Batters vs {st.session_state['pitcher_name']}"
    )

    st.caption("Click a hitter row to see the batter and pitcher pitch-type splits used for that matchup.")

    shown_columns = [
    "Hitter Name",
    "abs",
    "Prob 1 hit",
    "Kalshi 1 hit %",
    "Prob 2 hits",
    "Kalshi 2 hit %",
    ]

    display_df = df[
        [col for col in shown_columns if col in df.columns]
    ]

    table_event = st.dataframe(
        display_df,
        width="stretch",
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="hitter_results_table",
    )

    # csv = display_df.to_csv(index=False).encode("utf-8")

    # st.download_button(
    #     "Download table as CSV",
    #     data=csv,
    #     file_name=(
    #         f"{st.session_state['team2_name']}_vs_"
    #         f"{st.session_state['pitcher_name']}.csv"
    #     ).replace(" ", "_"),
    #     mime="text/csv",
    # )

    selected_rows = table_event.selection.rows

    if selected_rows:
        selected_index = selected_rows[0]

        selected_hitter_name = df.iloc[selected_index]["Hitter Name"]
        selected_hitter_id = df.iloc[selected_index]["Hitter ID"]

        st.session_state["selected_hitter_name"] = selected_hitter_name
        st.session_state["selected_hitter_id"] = selected_hitter_id

        st.divider()

        st.subheader(
            f"Matchup Splits: {selected_hitter_name} vs {st.session_state['pitcher_name']}"
        )

        hitter_df, pitcher_df, hitter_label, pitcher_label = calc.get_matchup_detail_data(
            hitter_id=selected_hitter_id,
            pitcher_id=st.session_state["pitcher_id"],
            vs_right=st.session_state["vs_right"],
            vs_left=st.session_state["vs_left"],
        )

        batter_col, pitcher_col = st.columns(2)

        with batter_col:
            st.markdown(f"### Batter Side")
            st.caption(hitter_label)

            st.dataframe(
                hitter_df,
                width="stretch",
                hide_index=True,
            )

        with pitcher_col:
            st.markdown(f"### Pitcher Side")
            st.caption(pitcher_label)

            st.dataframe(
                pitcher_df,
                width="stretch",
                hide_index=True,
            )

if st.session_state.get("show_history", False):
    st.divider()
    st.subheader("Bet History")

    history_col1, history_col2 = st.columns([1, 4])

    with history_col1:
        if st.button("Update Results"):
            try:
                fill_blanks()
                st.success("Updated open bet results.")
            except Exception as exc:
                st.error(f"Could not update results: {exc}")

    try:
        # fill_blanks()
        history_rows = search_db(only_open=False)
        if history_rows:
            st.dataframe(
                pd.DataFrame(history_rows),
                width="stretch",
                hide_index=True,
            )
        else:
            st.info("No bet entries saved yet.")
    except Exception as exc:
        st.error(f"Could not load history: {exc}")

# -----------------------------
# Create Line / Bet Entry Form
# -----------------------------
if create_line_clicked:
    st.session_state["show_create_line_form"] = True

if st.session_state.get("show_create_line_form"):
    st.divider()
    st.subheader("Create Bet Entry")

    if "selected_hitter_name" not in st.session_state:
        st.warning("Click a hitter row first, then click Create Line.")

    else:
        selected_player = st.session_state["selected_hitter_name"]
        st.caption(f"Selected player: {selected_player}")

        try:
            raw_odds = get_kalshi_hit_odds(selected_player)

            if isinstance(raw_odds, pd.DataFrame):
                odds_list = raw_odds["american_odds"].astype(str).tolist()

            elif isinstance(raw_odds, pd.Series):
                odds_list = raw_odds.astype(str).tolist()

            elif isinstance(raw_odds, (list, tuple)):
                odds_list = [str(odds) for odds in raw_odds]

            else:
                odds_list = []

        except Exception as exc:
            odds_list = []
            st.error(f"Could not fetch odds for {selected_player}: {exc}")

        if len(odds_list) < 3:
            st.warning("Expected odds for 0 hits, 1+ hits, and 2+ hits, but fewer were found.")

        else:
            hit_odds = {
                "0 hits": odds_list[0],
                "1+ hits": odds_list[1],
                "2+ hits": odds_list[2],
            }

            line_values = {
                "0 hits": 0,
                "1+ hits": 0.5,
                "2+ hits": 1.5,
            }

            st.markdown("Choose the line you want to bet:")

            line_cols = st.columns(3)
            line_order = ["0 hits", "1+ hits", "2+ hits"]

            for i, line_label in enumerate(line_order):
                odds = hit_odds.get(line_label)

                with line_cols[i]:
                    if st.button(
                        f"{line_label}\n{odds}",
                        key=f"select_line_{line_label}",
                        width="stretch",
                    ):
                        st.session_state["selected_bet_label"] = line_label
                        st.session_state["selected_bet_line"] = line_values[line_label]
                        st.session_state["selected_bet_odds"] = odds

            if "selected_bet_line" in st.session_state:
                st.success(
                    f"Selected: {st.session_state['selected_bet_label']} "
                    f"at {st.session_state['selected_bet_odds']}"
                )

                with st.form("save_bet_form"):
                    wager = st.number_input(
                        "Wager",
                        min_value=0.0,
                        step=1.0,
                        value=10.0,
                    )

                    submitted = st.form_submit_button("Save Bet")

                    if submitted:
                        create_entry(
                            name=selected_player,
                            line=st.session_state["selected_bet_line"],
                            odds=st.session_state["selected_bet_odds"],
                            wager=wager,
                        )

                        st.success(
                            f"Saved {selected_player}: "
                            f"{st.session_state['selected_bet_label']} "
                            f"line {st.session_state['selected_bet_line']} "
                            f"at {st.session_state['selected_bet_odds']} "
                            f"for ${wager:.2f}"
                        )
                        st.session_state["show_create_line_form"] = False
    