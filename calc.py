import warnings
from datetime import datetime
from math import comb

import requests
import streamlit as st
import pandas as pd
from pybaseball import cache, statcast, statcast_batter, statcast_pitcher

# --- MLB Teams ---
teams = {
    "Arizona Diamondbacks": 109,
    "Atlanta Braves": 144,
    "Baltimore Orioles": 110,
    "Boston Red Sox": 111,
    "Chicago Cubs": 112,
    "Chicago White Sox": 145,
    "Cincinnati Reds": 113,
    "Cleveland Guardians": 114,
    "Colorado Rockies": 115,
    "Detroit Tigers": 116,
    "Houston Astros": 117,
    "Kansas City Royals": 118,
    "Los Angeles Angels": 108,
    "Los Angeles Dodgers": 119,
    "Miami Marlins": 146,
    "Milwaukee Brewers": 158,
    "Minnesota Twins": 142,
    "New York Mets": 121,
    "New York Yankees": 147,
    "Oakland Athletics": 133,
    "Philadelphia Phillies": 143,
    "Pittsburgh Pirates": 134,
    "San Diego Padres": 135,
    "San Francisco Giants": 137,
    "Seattle Mariners": 136,
    "St. Louis Cardinals": 138,
    "Tampa Bay Rays": 139,
    "Texas Rangers": 140,
    "Toronto Blue Jays": 141,
    "Washington Nationals": 120,
}

PITCH_NAMES = {
    "FF": "Four-Seam Fastball",
    "SI": "Sinker",
    "FC": "Cutter",
    "SL": "Slider",
    "ST": "Sweeper",
    "CU": "Curveball",
    "KC": "Knuckle Curve",
    "CH": "Changeup",
    "FS": "Split-Finger",
    "KN": "Knuckleball",
    "SV": "Slurve",
    "SC": "Screwball",
    "EP": "Eephus",
    "CS": "Slow Curve",
    "FA": "Fastball",
}

PITCH_CONSOLIDATION = {
    "Curveball": "Curveball",
    "Knuckle Curve": "Curveball",
    "Slurve": "Curveball",
    "Slow Curve": "Curveball",
}

NON_PITCHES = ["IN", "AB", "PO"]


def consolidate_pitch(name):
    return PITCH_CONSOLIDATION.get(name, name)


def safe_float(value):
    try:
        if value in (None, "", ".---"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


@st.cache_data(show_spinner=False)
def get_league_averages():
    current_year = datetime.now().year
    start_date = f"{current_year}-03-01"
    end_date = datetime.now().strftime("%Y-%m-%d")

    df = statcast(start_dt=start_date, end_dt=end_date)

    if df is None or df.empty:
        return {}

    df = df[df["pitch_type"].notna()]
    df = df[df["pitch_type"].str.strip() != ""]
    df = df[~df["pitch_type"].isin(NON_PITCHES)]
    df["pitch_name"] = df["pitch_type"].map(
        lambda x: consolidate_pitch(PITCH_NAMES.get(x, x))
    )

    league_avgs = {}

    for stand in ["R", "L"]:
        subset = df[df["stand"] == stand]
        league_avgs[stand] = {}

        for pitch, group in subset.groupby("pitch_name"):
            xba_vals = group["estimated_ba_using_speedangle"].dropna()
            xslg_vals = group["estimated_slg_using_speedangle"].dropna()

            league_avgs[stand][pitch] = {
                "xba": round(xba_vals.mean(), 3) if not xba_vals.empty else None,
                "xslg": round(xslg_vals.mean(), 3) if not xslg_vals.empty else None,
            }

    return league_avgs


@st.cache_data(show_spinner=False)
def get_pitchers(team_id):
    url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster?rosterType=active"
    response = requests.get(url, timeout=20)
    data = response.json()

    return {
        p["person"]["fullName"]: p["person"]["id"]
        for p in data.get("roster", [])
        if p.get("position", {}).get("code") == "1"
    }


@st.cache_data(show_spinner=False)
def get_hitters(team_id):
    url = f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster?rosterType=active"
    response = requests.get(url, timeout=20)
    data = response.json()

    return {
        p["person"]["fullName"]: p["person"]["id"]
        for p in data.get("roster", [])
        if p.get("position", {}).get("code") != "1"
    }


@st.cache_data(show_spinner=False)
def get_pitch_data(player_id):
    current_year = datetime.now().year
    start_date = f"{current_year}-03-01"
    end_date = datetime.now().strftime("%Y-%m-%d")

    df = statcast_pitcher(start_date, end_date, player_id)

    if df is None or df.empty:
        return None, None

    df = df[df["pitch_type"].notna()]
    df = df[df["pitch_type"].str.strip() != ""]
    df = df[~df["pitch_type"].isin(NON_PITCHES)]
    df["pitch_name"] = df["pitch_type"].map(
        lambda x: consolidate_pitch(PITCH_NAMES.get(x, x))
    )

    def summarize(subset):
        total = len(subset)
        result = {}

        if total == 0:
            return result

        for pitch, group in subset.groupby("pitch_name"):
            usage = round(len(group) / total * 100, 1)
            xba_vals = group["estimated_ba_using_speedangle"].dropna()
            xslg_vals = group["estimated_slg_using_speedangle"].dropna()

            xba = round(xba_vals.mean(), 3) if not xba_vals.empty else None
            xslg = round(xslg_vals.mean(), 3) if not xslg_vals.empty else None

            result[pitch] = {
                "usage": usage,
                "xba": xba,
                "xslg": xslg,
                "n": len(group),
            }

        return dict(sorted(result.items(), key=lambda x: -x[1]["usage"]))

    return summarize(df[df["stand"] == "R"]), summarize(df[df["stand"] == "L"])


@st.cache_data(show_spinner=False)
def get_batter_pitch_data(player_id):
    current_year = datetime.now().year
    start_date = f"{current_year}-03-01"
    end_date = datetime.now().strftime("%Y-%m-%d")

    df = statcast_batter(start_date, end_date, player_id)

    if df is None or df.empty:
        return None, None

    df = df[df["pitch_type"].notna()]
    df = df[df["pitch_type"].str.strip() != ""]
    df = df[~df["pitch_type"].isin(NON_PITCHES)]
    df["pitch_name"] = df["pitch_type"].map(
        lambda x: consolidate_pitch(PITCH_NAMES.get(x, x))
    )

    def summarize(subset):
        total = len(subset)
        result = {}

        if total == 0:
            return result

        for pitch, group in subset.groupby("pitch_name"):
            usage = round(len(group) / total * 100, 1)
            xba_vals = group["estimated_ba_using_speedangle"].dropna()
            xslg_vals = group["estimated_slg_using_speedangle"].dropna()

            xba = round(xba_vals.mean(), 3) if not xba_vals.empty else None
            xslg = round(xslg_vals.mean(), 3) if not xslg_vals.empty else None

            result[pitch] = {
                "usage": usage,
                "xba": xba,
                "xslg": xslg,
                "n": len(group),
            }

        return dict(sorted(result.items(), key=lambda x: -x[1]["usage"]))

    return summarize(df[df["p_throws"] == "R"]), summarize(df[df["p_throws"] == "L"])


@st.cache_data(show_spinner=False)
def get_player_handedness(player_id):
    url = f"https://statsapi.mlb.com/api/v1/people/{player_id}"
    response = requests.get(url, timeout=20)
    data = response.json()

    people = data.get("people", [])

    if not people:
        return None, None

    p = people[0]

    bat_side = p.get("batSide", {}).get("code")
    pitch_hand = p.get("pitchHand", {}).get("code")

    return bat_side, pitch_hand


@st.cache_data(show_spinner=False)
def get_pitcher_k_rate(pitcher_id):
    current_year = datetime.now().year

    url = (
        f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats"
        f"?stats=season&season={current_year}&group=pitching"
    )

    response = requests.get(url, timeout=20)
    data = response.json()

    stats = data.get("stats", [])

    if not stats:
        return None

    splits = stats[0].get("splits", [])

    if not splits:
        return None

    s = splits[0].get("stat", {})

    ks = safe_int(s.get("strikeOuts", 0))
    bf = safe_int(s.get("battersFaced", 0))

    if bf == 0:
        return None

    return round(ks / bf, 4)


def get_league_avg_k_rate():
    return 0.225


def get_pitcher_k_ratio(pitcher_id):
    pitcher_k = get_pitcher_k_rate(pitcher_id)
    league_k = get_league_avg_k_rate()

    if pitcher_k is None:
        return None, league_k, None

    ratio = round(pitcher_k / league_k, 4)

    return pitcher_k, league_k, ratio


def compute_matchup_ba(vs_pitcher_split, vs_hitter_split, league_data):
    if vs_pitcher_split is None or vs_hitter_split is None:
        return None

    total = 0.0
    pitches_used = 0

    for pitch, pitcher_stats in vs_pitcher_split.items():
        usage = pitcher_stats["usage"] / 100
        p_xba = pitcher_stats["xba"]
        lg_xba = league_data.get(pitch, {}).get("xba")

        h_stats = vs_hitter_split.get(pitch)
        h_xba = h_stats["xba"] if h_stats else None

        if p_xba is None or lg_xba is None or h_xba is None:
            continue

        total += usage * (p_xba / lg_xba) * h_xba
        pitches_used += 1

    return round(total, 3) if pitches_used > 0 else None


def compute_matchup_slg(vs_pitcher_split, vs_hitter_split, league_data):
    if vs_pitcher_split is None or vs_hitter_split is None:
        return None

    total = 0.0
    pitches_used = 0

    for pitch, pitcher_stats in vs_pitcher_split.items():
        usage = pitcher_stats["usage"] / 100
        p_xslg = pitcher_stats["xslg"]
        lg_xslg = league_data.get(pitch, {}).get("xslg")

        h_stats = vs_hitter_split.get(pitch)
        h_xslg = h_stats["xslg"] if h_stats else None

        if p_xslg is None or lg_xslg is None or h_xslg is None:
            continue

        total += usage * (p_xslg / lg_xslg) * h_xslg
        pitches_used += 1

    return round(total, 3) if pitches_used > 0 else None


@st.cache_data(show_spinner=False)
def get_batter_season_stats(player_id):
    current_year = datetime.now().year

    url = (
        f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
        f"?stats=season&season={current_year}&group=hitting"
    )

    response = requests.get(url, timeout=20)
    data = response.json()

    splits = data.get("stats", [{}])[0].get("splits", [])

    if not splits:
        return None, None, None

    s = splits[0].get("stat", {})

    avg = s.get("avg")
    slg = s.get("slg")

    ab = safe_int(s.get("atBats", 0))
    games = safe_int(s.get("gamesPlayed", 1), default=1)

    ab_per_g = round(ab / games, 2) if games > 0 else None

    return avg, slg, ab_per_g


@st.cache_data(show_spinner=False)
def get_batter_k_rate(player_id):
    current_year = datetime.now().year

    url = (
        f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
        f"?stats=season&season={current_year}&group=hitting"
    )

    response = requests.get(url, timeout=20)
    data = response.json()

    splits = data.get("stats", [{}])[0].get("splits", [])

    if not splits:
        return None, None, None

    s = splits[0].get("stat", {})

    pa = safe_int(s.get("plateAppearances", 0))
    ab = safe_int(s.get("atBats", 0))
    strikeouts = safe_int(s.get("strikeOuts", 0))
    walks = safe_int(s.get("baseOnBalls", 0))
    hbp = safe_int(s.get("hitByPitch", 0))

    if pa == 0 or ab == 0:
        return None, None, None

    k_rate_per_ab = round(strikeouts / ab, 4)
    walk_rate_per_pa = round((walks + hbp) / pa, 4)
    contact_rate_per_ab = round(1 - k_rate_per_ab, 4)

    return contact_rate_per_ab, k_rate_per_ab, walk_rate_per_pa


@st.cache_data(show_spinner=False)
def get_batter_hit_breakdown(player_id):
    current_year = datetime.now().year

    url = (
        f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
        f"?stats=season&season={current_year}&group=hitting"
    )

    response = requests.get(url, timeout=20)
    data = response.json()

    splits = data.get("stats", [{}])[0].get("splits", [])

    if not splits:
        return None

    s = splits[0].get("stat", {})

    singles = (
        safe_int(s.get("hits", 0))
        - safe_int(s.get("doubles", 0))
        - safe_int(s.get("triples", 0))
        - safe_int(s.get("homeRuns", 0))
    )

    doubles = safe_int(s.get("doubles", 0))
    triples = safe_int(s.get("triples", 0))
    hrs = safe_int(s.get("homeRuns", 0))

    tb = singles * 1 + doubles * 2 + triples * 3 + hrs * 4

    if tb == 0:
        return None

    return {
        "singles": {
            "count": singles,
            "tb": singles * 1,
            "pct": round(singles * 1 / tb * 100, 1),
        },
        "doubles": {
            "count": doubles,
            "tb": doubles * 2,
            "pct": round(doubles * 2 / tb * 100, 1),
        },
        "triples": {
            "count": triples,
            "tb": triples * 3,
            "pct": round(triples * 3 / tb * 100, 1),
        },
        "home_runs": {
            "count": hrs,
            "tb": hrs * 4,
            "pct": round(hrs * 4 / tb * 100, 1),
        },
        "total_tb": tb,
    }


def compute_betting_odds(
    matchup_ba,
    matchup_slg,
    season_avg_f,
    season_slg_f,
    hit_breakdown,
    k_rate_per_ab,
    ab_per_game,
):
    results = {}

    def to_ml(prob):
        if prob is None:
            return "N/A"

        if prob >= 1.0:
            return "+∞"

        if prob <= 0.0:
            return "N/A"

        if prob > 0.5:
            return f"-{round((prob / (1 - prob)) * 100)}"

        return f"+{round(((1 - prob) / prob) * 100)}"

    total_abs = ab_per_game if ab_per_game is not None else 3.5
    matchup_abs = min(2.5, total_abs)
    season_abs = max(0.0, total_abs - matchup_abs)

    kr = k_rate_per_ab if k_rate_per_ab is not None else 0.0

    m_ba = matchup_ba * (1 - kr) if matchup_ba is not None else None
    m_slg = matchup_slg * (1 - kr) if matchup_slg is not None else None
    s_ba = season_avg_f * (1 - kr) if season_avg_f is not None else None
    s_slg = season_slg_f * (1 - kr) if season_slg_f is not None else None

    # --- At least 1 hit ---
    if m_ba is not None and s_ba is not None:
        p_no_hit = ((1 - m_ba) ** matchup_abs) * ((1 - s_ba) ** season_abs)
        p_1hit = round(1 - p_no_hit, 4)

        results["1_hit"] = {
            "prob": p_1hit,
            "ml": to_ml(p_1hit),
        }
    else:
        results["1_hit"] = {
            "prob": None,
            "ml": "N/A",
        }

    # --- At least 2 hits ---
    if m_ba is not None and s_ba is not None and total_abs > 0:
        eff_ba = (m_ba * matchup_abs + s_ba * season_abs) / total_abs

        p0 = (1 - eff_ba) ** total_abs
        p1 = total_abs * eff_ba * ((1 - eff_ba) ** (total_abs - 1))
        p_2hit = round(1 - p0 - p1, 4)

        results["2_hits"] = {
            "prob": p_2hit,
            "ml": to_ml(p_2hit),
        }
    else:
        results["2_hits"] = {
            "prob": None,
            "ml": "N/A",
        }

    # --- At least 3 hits ---
    if m_ba is not None and s_ba is not None and total_abs > 0:
        eff_ba = (m_ba * matchup_abs + s_ba * season_abs) / total_abs

        p0 = (1 - eff_ba) ** total_abs
        p1 = total_abs * eff_ba * ((1 - eff_ba) ** (total_abs - 1))

        p2 = (
            comb(int(total_abs), 2)
            * (eff_ba**2)
            * ((1 - eff_ba) ** (total_abs - 2))
            if total_abs >= 2
            else 0
        )

        p_3hit = round(1 - p0 - p1 - p2, 4)

        results["3_hits"] = {
            "prob": p_3hit,
            "ml": to_ml(p_3hit),
        }
    else:
        results["3_hits"] = {
            "prob": None,
            "ml": "N/A",
        }

    # --- At least 2 TB ---
    if (
        hit_breakdown
        and m_ba is not None
        and s_ba is not None
        and m_slg is not None
        and s_slg is not None
    ):
        total_hits = sum(
            hit_breakdown[k]["count"]
            for k in ["singles", "doubles", "triples", "home_runs"]
        )

        if total_hits > 0:
            hr_tb_pct = hit_breakdown["home_runs"]["pct"] / 100

            hr_share = hit_breakdown["home_runs"]["count"] / total_hits
            dbl_share = hit_breakdown["doubles"]["count"] / total_hits
            trp_share = hit_breakdown["triples"]["count"] / total_hits
            sng_share = hit_breakdown["singles"]["count"] / total_hits

            def blend(ba_prob, slg_prob):
                return (ba_prob * 2 + slg_prob) / 3

            mm_sng = blend(
                m_ba * sng_share,
                m_slg * (hit_breakdown["singles"]["pct"] / 100) / 1,
            )
            mm_dbl = blend(
                m_ba * dbl_share,
                m_slg * (hit_breakdown["doubles"]["pct"] / 100) / 2,
            )
            mm_trp = blend(
                m_ba * trp_share,
                m_slg * (hit_breakdown["triples"]["pct"] / 100) / 3,
            )
            mm_hr = blend(
                m_ba * hr_share,
                m_slg * hr_tb_pct / 4,
            )

            ss_sng = blend(
                s_ba * sng_share,
                s_slg * (hit_breakdown["singles"]["pct"] / 100) / 1,
            )
            ss_dbl = blend(
                s_ba * dbl_share,
                s_slg * (hit_breakdown["doubles"]["pct"] / 100) / 2,
            )
            ss_trp = blend(
                s_ba * trp_share,
                s_slg * (hit_breakdown["triples"]["pct"] / 100) / 3,
            )
            ss_hr = blend(
                s_ba * hr_share,
                s_slg * hr_tb_pct / 4,
            )

            m_no_hit = 1 - mm_sng - mm_dbl - mm_trp - mm_hr
            s_no_hit = 1 - ss_sng - ss_dbl - ss_trp - ss_hr

            p_no_hit = (m_no_hit**matchup_abs) * (s_no_hit**season_abs)

            p_one_single = (
                matchup_abs
                * mm_sng
                * (m_no_hit ** (matchup_abs - 1))
                * (s_no_hit**season_abs)
            ) + (
                season_abs
                * ss_sng
                * (m_no_hit**matchup_abs)
                * (s_no_hit ** (season_abs - 1))
            )

            p_2tb = round(1 - p_no_hit - p_one_single, 4)

            results["2_tb"] = {
                "prob": p_2tb,
                "ml": to_ml(p_2tb),
            }
        else:
            results["2_tb"] = {
                "prob": None,
                "ml": "N/A",
            }
    else:
        results["2_tb"] = {
            "prob": None,
            "ml": "N/A",
        }

    # --- Home Run ---
    hr_prob_ba = None
    hr_prob_slg = None

    if hit_breakdown and m_ba is not None and s_ba is not None:
        total_hits = sum(
            hit_breakdown[k]["count"]
            for k in ["singles", "doubles", "triples", "home_runs"]
        )

        if total_hits > 0:
            hr_share = hit_breakdown["home_runs"]["count"] / total_hits
            matchup_hr_p = m_ba * hr_share
            season_hr_p = s_ba * hr_share

            p_no_hr = ((1 - matchup_hr_p) ** matchup_abs) * (
                (1 - season_hr_p) ** season_abs
            )

            hr_prob_ba = round(1 - p_no_hr, 4)

    if hit_breakdown and m_slg is not None and s_slg is not None:
        hr_tb_pct = hit_breakdown["home_runs"]["pct"] / 100

        matchup_hr_p = m_slg * hr_tb_pct / 4
        season_hr_p = s_slg * hr_tb_pct / 4

        p_no_hr = ((1 - matchup_hr_p) ** matchup_abs) * (
            (1 - season_hr_p) ** season_abs
        )

        hr_prob_slg = round(1 - p_no_hr, 4)

    if hr_prob_ba is not None and hr_prob_slg is not None:
        hr_final = round((hr_prob_ba + hr_prob_slg) / 2, 4)
    elif hr_prob_ba is not None:
        hr_final = hr_prob_ba
    elif hr_prob_slg is not None:
        hr_final = hr_prob_slg
    else:
        hr_final = None

    results["hr"] = {
        "prob": hr_final,
        "ml": to_ml(hr_final),
    }

    return results


def format_probability(prob):
    if prob is None:
        return "N/A"

    return f"{prob * 100:.1f}%"


def build_hitter_row(
    hitter_name,
    hitter_id,
    pitcher_id,
    vs_right,
    vs_left,
    lg_right,
    lg_left,
    pitcher_k_ratio,
):
    vs_rhp, vs_lhp = get_batter_pitch_data(hitter_id)

    if vs_rhp is None and vs_lhp is None:
        return {
            "Hitter Name": hitter_name,
            "abs": "N/A",
            "Prob 1 hit": "N/A",
            "Prob 2 hits": "N/A",
        }

    bat_side, _ = get_player_handedness(hitter_id)
    _, pitch_hand = get_player_handedness(pitcher_id)

    if bat_side == "S":
        effective_bat_side = "L" if pitch_hand == "R" else "R"
    else:
        effective_bat_side = bat_side

    pitcher_relevant_split = vs_right if effective_bat_side == "R" else vs_left
    hitter_relevant_split = vs_rhp if pitch_hand == "R" else vs_lhp
    relevant_league = lg_right if effective_bat_side == "R" else lg_left

    matchup_ba = compute_matchup_ba(
        pitcher_relevant_split,
        hitter_relevant_split,
        relevant_league,
    )

    matchup_slg = compute_matchup_slg(
        pitcher_relevant_split,
        hitter_relevant_split,
        relevant_league,
    )

    season_avg, season_slg, ab_per_game = get_batter_season_stats(hitter_id)

    season_avg_f = safe_float(season_avg)
    season_slg_f = safe_float(season_slg)

    _, k_rate_per_ab, _ = get_batter_k_rate(hitter_id)

    if k_rate_per_ab is not None and pitcher_k_ratio is not None:
        k_rate_adjusted = min(round(k_rate_per_ab * pitcher_k_ratio, 4), 0.99)
    else:
        k_rate_adjusted = k_rate_per_ab

    hit_breakdown = get_batter_hit_breakdown(hitter_id)

    betting_odds = compute_betting_odds(
        matchup_ba,
        matchup_slg,
        season_avg_f,
        season_slg_f,
        hit_breakdown,
        k_rate_adjusted,
        ab_per_game,
    )

    return {
        "Hitter Name": hitter_name,
        "abs": ab_per_game if ab_per_game is not None else "N/A",

        "Prob 1 hit": format_probability(betting_odds["1_hit"]["prob"]),
        "1 Hit ML": betting_odds["1_hit"]["ml"],

        "Prob 2 hits": format_probability(betting_odds["2_hits"]["prob"]),
        "2 Hit ML": betting_odds["2_hits"]["ml"],
    }

def split_dict_to_df(split_dict):
    """
    Converts one pitch split dictionary into a displayable dataframe.
    """
    if not split_dict:
        return pd.DataFrame(
            columns=[
                "Pitch",
                "Usage %",
                "xBA",
                "xSLG",
                "Samples",
            ]
        )

    rows = []

    for pitch, stats in split_dict.items():
        rows.append({
            "Pitch": pitch,
            "Usage %": stats.get("usage"),
            "xBA": stats.get("xba"),
            "xSLG": stats.get("xslg"),
            "Samples": stats.get("n"),
        })

    return pd.DataFrame(rows)


def get_matchup_detail_data(
    hitter_id,
    pitcher_id,
    vs_right,
    vs_left,
):
    """
    Gets the hitter split and pitcher split that are actually used
    in the matchup calculation.
    """
    vs_rhp, vs_lhp = get_batter_pitch_data(hitter_id)

    bat_side, _ = get_player_handedness(hitter_id)
    _, pitch_hand = get_player_handedness(pitcher_id)

    if bat_side == "S":
        effective_bat_side = "L" if pitch_hand == "R" else "R"
    else:
        effective_bat_side = bat_side

    pitcher_relevant_split = vs_right if effective_bat_side == "R" else vs_left
    hitter_relevant_split = vs_rhp if pitch_hand == "R" else vs_lhp

    hitter_label = f"Batter vs {pitch_hand}HP"
    pitcher_label = f"Pitcher vs {effective_bat_side}HB"

    hitter_df = split_dict_to_df(hitter_relevant_split)
    pitcher_df = split_dict_to_df(pitcher_relevant_split)

    return hitter_df, pitcher_df, hitter_label, pitcher_label