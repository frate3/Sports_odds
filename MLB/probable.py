import os
import sys
import contextlib
from datetime import datetime

import pandas as pd
import requests
import logging

import MLB.calc as calc


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "data", "matchup_cache")
logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)

TEAM_NAME_ALIASES = {
    "Athletics": "Oakland Athletics",
}

def normalize_team_name(team_name):
    return TEAM_NAME_ALIASES.get(team_name, team_name)

@contextlib.contextmanager
def suppress_terminal_output():
    """
    Hides noisy print output from pybaseball/statcast calls.
    """
    with open(os.devnull, "w") as devnull:
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        try:
            sys.stdout = devnull
            sys.stderr = devnull
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

def get_today_probable_games():
    today = datetime.now().strftime("%Y-%m-%d")

    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={today}&hydrate=probablePitcher,team"
    )

    response = requests.get(url, timeout=20)
    response.raise_for_status()
    data = response.json()

    games = []

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            away = game["teams"]["away"]
            home = game["teams"]["home"]

            away_team = normalize_team_name(away["team"]["name"])
            home_team = normalize_team_name(home["team"]["name"])

            away_pitcher = away.get("probablePitcher")
            home_pitcher = home.get("probablePitcher")

            games.append({
            "away_team": away_team,
            "away_team_id": away["team"]["id"],

            "home_team": home_team,
            "home_team_id": home["team"]["id"],

            "away_pitcher_name": away_pitcher.get("fullName") if away_pitcher else None,
            "away_pitcher_id": away_pitcher.get("id") if away_pitcher else None,

            "home_pitcher_name": home_pitcher.get("fullName") if home_pitcher else None,
            "home_pitcher_id": home_pitcher.get("id") if home_pitcher else None,
        })

    return games


def safe_filename(text):
    return (
        str(text)
        .replace(" ", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(":", "")
    )


def get_matchup_cache_path(today, hitter_team_name, pitcher_name):
    """Return the exact CSV path used for one precomputed matchup."""
    filename = (
        f"{today}__{safe_filename(hitter_team_name)}_hitters_vs_"
        f"{safe_filename(pitcher_name)}.csv"
    )
    return os.path.join(CACHE_DIR, filename)


def cache_file_is_usable(path):
    """
    True only when the CSV already exists, is not empty,
    and can be opened by pandas.
    """
    if not os.path.exists(path):
        return False

    if os.path.getsize(path) == 0:
        return False

    try:
        pd.read_csv(path, nrows=1)
        return True
    except Exception:
        return False


def build_matchup_table(pitcher_id, pitcher_name, hitter_team_name): 
    hitter_team_id = calc.teams[hitter_team_name]

    print(f"\nStarting matchup: {hitter_team_name} hitters vs {pitcher_name}")

    with suppress_terminal_output():
        hitters = calc.get_hitters(hitter_team_id)

    if not hitters:
        print(f"No hitters found for {hitter_team_name}")
        return None

    print(f"Found {len(hitters)} hitters")

    print("Loading pitcher data...")
    with suppress_terminal_output():
        vs_right, vs_left = calc.get_pitch_data(pitcher_id)

    if vs_right is None and vs_left is None:
        print(f"No Statcast data found for pitcher: {pitcher_name}")
        return None

    print("Loading league averages...")
    with suppress_terminal_output():
        league_avgs = calc.get_league_averages()

    lg_right = league_avgs.get("R", {})
    lg_left = league_avgs.get("L", {})

    print("Loading pitcher K ratio...")
    with suppress_terminal_output():
        pitcher_k_rate, league_k_rate, pitcher_k_ratio = calc.get_pitcher_k_ratio(
            pitcher_id
        )

    rows = []
    hitter_items = list(hitters.items())
    total_hitters = len(hitter_items)

    for i, (hitter_name, hitter_id) in enumerate(hitter_items, start=1):
        percent = round(i / total_hitters * 100, 1)

        print(
            f"\rProgress: {percent}% "
            f"({i}/{total_hitters}) - {hitter_name} - {str(datetime.now())[11:16]}",
            end="",
            flush=True,
        )

        try:
            with suppress_terminal_output():
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

            row["Hitter ID"] = hitter_id
            row["Pitcher Name"] = pitcher_name
            row["Pitcher ID"] = pitcher_id
            row["Hitter Team"] = hitter_team_name

        except Exception as exc:
            row = {
                "Hitter Name": hitter_name,
                "abs": "Error",
                "Prob 1 hit": "Error",
                "1 Hit ML": "Error",
                "Prob 2 hits": "Error",
                "2 Hit ML": "Error",
                "Hitter ID": hitter_id,
                "Pitcher Name": pitcher_name,
                "Pitcher ID": pitcher_id,
                "Hitter Team": hitter_team_name,
                "Error": str(exc),
            }

        rows.append(row)

    print(f"\nFinished matchup: {hitter_team_name} hitters vs {pitcher_name}")

    return pd.DataFrame(rows)


def precompute_today_matchups():
    os.makedirs(CACHE_DIR, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    games = get_today_probable_games()

    saved_files = []
    skipped_existing_files = []

    matchups = []

    for game in games:
        away_team = game["away_team"]
        home_team = game["home_team"]

        if game["away_pitcher_id"]:
            matchups.append({
                "pitcher_id": game["away_pitcher_id"],
                "pitcher_name": game["away_pitcher_name"],
                "hitter_team_name": home_team,
            })

        if game["home_pitcher_id"]:
            matchups.append({
                "pitcher_id": game["home_pitcher_id"],
                "pitcher_name": game["home_pitcher_name"],
                "hitter_team_name": away_team,
            })

    total_matchups = len(matchups)

    print(f"Precomputing MLB matchups for {today}")
    print(f"Total matchups found: {total_matchups}")

    for matchup_index, matchup in enumerate(matchups, start=1):
        pitcher_id = matchup["pitcher_id"]
        pitcher_name = matchup["pitcher_name"]
        hitter_team_name = matchup["hitter_team_name"]

        overall_percent = round(matchup_index / total_matchups * 100, 1)
        path = get_matchup_cache_path(today, hitter_team_name, pitcher_name)

        print("\n" + "=" * 60)
        print(
            f"Overall: {overall_percent}% "
            f"({matchup_index}/{total_matchups})"
        )
        print(f"Matchup: {hitter_team_name} hitters vs {pitcher_name}")
        print("=" * 60)

        if cache_file_is_usable(path):
            skipped_existing_files.append(path)
            print(f"Already exists, skipping: {path}")
            continue

        df = build_matchup_table(
            pitcher_id=pitcher_id,
            pitcher_name=pitcher_name,
            hitter_team_name=hitter_team_name,
        )

        if df is not None:
            temp_path = f"{path}.tmp"
            df.to_csv(temp_path, index=False)
            os.replace(temp_path, path)
            saved_files.append(path)

            print(f"Saved: {path}")
        else:
            print(f"Skipped: {hitter_team_name} hitters vs {pitcher_name}")

    print("\nDone.")
    print(f"Saved {len(saved_files)} new matchup files.")
    print(f"Skipped {len(skipped_existing_files)} existing matchup files.")

    if saved_files:
        print("\nNew files:")
        for path in saved_files:
            print(path)

    if skipped_existing_files:
        print("\nExisting files skipped:")
        for path in skipped_existing_files:
            print(path)


if __name__ == "__main__":
    
    precompute_today_matchups()