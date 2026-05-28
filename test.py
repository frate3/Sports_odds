import pytest
import pandas as pd
from pandas.testing import assert_frame_equal, assert_series_equal
from pybaseball.plotting import transform_coordinates
from datetime import datetime
import requests


def get_probable_pitchers_for_today():
    today = datetime.now().strftime("%Y-%m-%d")

    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={today}&hydrate=probablePitcher,team"
    )

    response = requests.get(url, timeout=20)
    response.raise_for_status()
    data = response.json()

    probable = {}

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            away_team = game["teams"]["away"]["team"]["name"]
            home_team = game["teams"]["home"]["team"]["name"]

            away_pitcher = game["teams"]["away"].get("probablePitcher")
            home_pitcher = game["teams"]["home"].get("probablePitcher")

            probable[away_team] = {
                "opponent": home_team,
                "pitcher_name": away_pitcher.get("fullName") if away_pitcher else None,
                "pitcher_id": away_pitcher.get("id") if away_pitcher else None,
            }

            probable[home_team] = {
                "opponent": away_team,
                "pitcher_name": home_pitcher.get("fullName") if home_pitcher else None,
                "pitcher_id": home_pitcher.get("id") if home_pitcher else None,
            }

    return probable

print(get_probable_pitchers_for_today())