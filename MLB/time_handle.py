import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests


OUTPUT_DIR = "data"
DEFAULT_TIMEZONE = "America/New_York"


def fetch_schedule(date=None):
    """
    Fetch MLB games for one date from the MLB Stats API.

    date format:
        YYYY-MM-DD

    If date is not provided, today's date is used.
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={date}&hydrate=probablePitcher,team,venue"
    )

    response = requests.get(url, timeout=20)
    response.raise_for_status()
    return response.json()


def format_game_time(game_date, timezone_name=DEFAULT_TIMEZONE):
    """
    MLB gameDate comes back in UTC.
    This converts it to your local display timezone.
    """
    if not game_date:
        return None, None

    utc_time = datetime.fromisoformat(game_date.replace("Z", "+00:00"))
    local_time = utc_time.astimezone(ZoneInfo(timezone_name))

    return (
        local_time.strftime("%Y-%m-%d"),
        local_time.strftime("%I:%M %p").lstrip("0"),
    )


def get_game_start_times(date=None, timezone_name=DEFAULT_TIMEZONE):
    """
    Returns a dataframe with each MLB game's start time.
    """
    data = fetch_schedule(date)
    rows = []

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            away = game["teams"]["away"]
            home = game["teams"]["home"]

            away_pitcher = away.get("probablePitcher")
            home_pitcher = home.get("probablePitcher")

            local_date, local_time = format_game_time(
                game.get("gameDate"),
                timezone_name=timezone_name,
            )

            rows.append({
                "game_pk": game.get("gamePk"),
                "date": local_date,
                "start_time": local_time,
                "away_team": away["team"]["name"],
                "home_team": home["team"]["name"],
                "matchup": f'{away["team"]["name"]} @ {home["team"]["name"]}',
                "away_probable_pitcher": away_pitcher.get("fullName") if away_pitcher else None,
                "home_probable_pitcher": home_pitcher.get("fullName") if home_pitcher else None,
                "venue": game.get("venue", {}).get("name"),
                "status": game.get("status", {}).get("detailedState"),
            })

    return pd.DataFrame(rows)


def save_game_start_times(date=None, timezone_name=DEFAULT_TIMEZONE):
    """
    Saves today's game start times to:
        data/game_start_times_YYYY-MM-DD.csv
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    df = get_game_start_times(date=date, timezone_name=timezone_name)

    filename = f"game_start_times_{date}.csv"
    path = os.path.join(OUTPUT_DIR, filename)

    df.to_csv(path, index=False)
    return df, path


if __name__ == "__main__":
    df, path = save_game_start_times()

    if df.empty:
        print("No MLB games found.")
    else:
        print(df[[
            "start_time",
            "matchup",
            "away_probable_pitcher",
            "home_probable_pitcher",
            "status",
        ]].to_string(index=False))

        print(f"\nSaved: {path}")
