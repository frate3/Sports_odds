import warnings
from datetime import datetime
from math import comb

import requests
import streamlit as st
import pandas as pd
import numpy as np
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

# Additional hit-chance adjustment settings from the updated notebook logic.
LEAGUE_AVG_SPRINT_SPEED = 27.0
SPEED_BABIP_SCALAR = 0.0015
SPEED_ADJ_CAP = 0.012
H2H_CREDIBILITY_K = 50

# Strike-zone grid used for the zone scalar adjustment.
# plate_x: negative = inside to RHB, positive = outside to RHB
ZONE_X_BINS = [-0.83, -0.28, 0.28, 0.83]
ZONE_Z_BINS = [1.5, 2.17, 2.83, 3.5]

# Raw data caches let the GUI keep the same function signatures while the
# calculation can still use zone-level Statcast data internally.
_LEAGUE_RAW_DF_CACHE = None
_PITCHER_RAW_DF_CACHE = {}
_BATTER_RAW_DF_CACHE = {}



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


def assign_zone(px, pz):
    """Map pitch coordinates to a 3x3 strike-zone bucket, or None if unavailable."""
    if px is None or pz is None:
        return None
    try:
        if np.isnan(px) or np.isnan(pz):
            return None
    except (TypeError, ValueError):
        return None

    col = next((i for i in range(3) if ZONE_X_BINS[i] <= px < ZONE_X_BINS[i + 1]), None)
    row = next((i for i in range(3) if ZONE_Z_BINS[i] <= pz < ZONE_Z_BINS[i + 1]), None)

    if col is None or row is None:
        return None

    return (col, row)


def get_pitcher_zone_profile_by_pitch(pitcher_df):
    """Return {stand: {pitch_name: {zone: pct}}} for where a pitcher throws each pitch."""
    result = {}

    if pitcher_df is None or pitcher_df.empty:
        return result

    for stand in ["R", "L"]:
        subset = pitcher_df[pitcher_df["stand"] == stand].copy()
        if subset.empty:
            result[stand] = {}
            continue

        subset["zone"] = subset.apply(lambda r: assign_zone(r.get("plate_x"), r.get("plate_z")), axis=1)
        subset = subset[subset["zone"].notna()]
        result[stand] = {}

        for pitch, group in subset.groupby("pitch_name"):
            total = len(group)
            counts = group["zone"].value_counts()
            result[stand][pitch] = {zone: round(count / total, 4) for zone, count in counts.items()}

    return result


def get_hitter_zone_profile_by_pitch(hitter_df):
    """Return {p_throws: {pitch_name: {zone: xBA}}}; requires at least 3 samples per cell."""
    result = {}

    if hitter_df is None or hitter_df.empty:
        return result

    for hand in ["R", "L"]:
        subset = hitter_df[hitter_df["p_throws"] == hand].copy()
        if subset.empty:
            result[hand] = {}
            continue

        subset["zone"] = subset.apply(lambda r: assign_zone(r.get("plate_x"), r.get("plate_z")), axis=1)
        subset = subset[subset["zone"].notna()]
        result[hand] = {}

        for pitch, group in subset.groupby("pitch_name"):
            result[hand][pitch] = {}

            for zone, zgroup in group.groupby("zone"):
                xba_vals = zgroup["estimated_ba_using_speedangle"].dropna()
                if len(xba_vals) >= 3:
                    result[hand][pitch][zone] = round(xba_vals.mean(), 3)

    return result


def get_league_zone_averages_by_pitch(league_df):
    """Return {stand: {pitch_name: {zone: league xBA}}}."""
    if league_df is None or league_df.empty:
        return {}

    result = {}

    for stand in ["R", "L"]:
        subset = league_df[league_df["stand"] == stand].copy()
        if subset.empty:
            result[stand] = {}
            continue

        subset["zone"] = subset.apply(lambda r: assign_zone(r.get("plate_x"), r.get("plate_z")), axis=1)
        subset = subset[subset["zone"].notna()]
        result[stand] = {}

        for pitch, group in subset.groupby("pitch_name"):
            result[stand][pitch] = {}

            for zone, zgroup in group.groupby("zone"):
                xba_vals = zgroup["estimated_ba_using_speedangle"].dropna()
                if not xba_vals.empty:
                    result[stand][pitch][zone] = round(xba_vals.mean(), 3)

    return result


def compute_zone_scalar_by_pitch(
    pitcher_zone_profile,
    hitter_zone_profile,
    league_zone_avgs,
    pitcher_pitch_summary,
    effective_bat_side,
    pitch_hand,
):
    """Blend per-pitch hot/cold zone overlap into one xBA scalar."""
    pitcher_zones = pitcher_zone_profile.get(effective_bat_side, {})
    hitter_zones = hitter_zone_profile.get(pitch_hand, {})
    league_zones = league_zone_avgs.get(effective_bat_side, {})

    if not pitcher_zones or not hitter_zones or not league_zones or not pitcher_pitch_summary:
        return None, {}

    total_scalar = 0.0
    total_weight = 0.0
    per_pitch = {}

    for pitch, usage_stats in pitcher_pitch_summary.items():
        usage_pct = usage_stats["usage"] / 100
        p_zones = pitcher_zones.get(pitch, {})
        h_zones = hitter_zones.get(pitch, {})
        lg_zones = league_zones.get(pitch, {})

        if not p_zones or not h_zones or not lg_zones:
            per_pitch[pitch] = {
                "scalar": None,
                "usage": usage_pct,
                "covered_pct": 0,
                "reason": "no zone data",
            }
            continue

        pitch_total = 0.0
        covered_pct = 0.0

        for zone, p_pct in p_zones.items():
            h_xba = h_zones.get(zone)
            lg_xba = lg_zones.get(zone)

            if h_xba is None or lg_xba is None or lg_xba == 0:
                continue

            pitch_total += p_pct * (h_xba / lg_xba)
            covered_pct += p_pct

        if covered_pct < 0.25:
            per_pitch[pitch] = {
                "scalar": None,
                "usage": usage_pct,
                "covered_pct": covered_pct,
                "reason": f"low coverage ({covered_pct:.0%})",
            }
            continue

        pitch_scalar = round(pitch_total / covered_pct, 4)
        per_pitch[pitch] = {
            "scalar": pitch_scalar,
            "usage": usage_pct,
            "covered_pct": covered_pct,
            "reason": None,
        }
        total_scalar += usage_pct * pitch_scalar
        total_weight += usage_pct

    if total_weight < 0.3:
        return None, per_pitch

    return round(total_scalar / total_weight, 4), per_pitch


@st.cache_data(show_spinner=False)
def get_league_averages():
    global _LEAGUE_RAW_DF_CACHE

    current_year = datetime.now().year
    start_date = f"{current_year}-03-01"
    end_date = datetime.now().strftime("%Y-%m-%d")

    df = statcast(start_dt=start_date, end_dt=end_date)

    if df is None or df.empty:
        _LEAGUE_RAW_DF_CACHE = None
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

    _LEAGUE_RAW_DF_CACHE = df

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
        _PITCHER_RAW_DF_CACHE[player_id] = None
        return None, None

    df = df[df["pitch_type"].notna()]
    df = df[df["pitch_type"].str.strip() != ""]
    df = df[~df["pitch_type"].isin(NON_PITCHES)]
    df["pitch_name"] = df["pitch_type"].map(
        lambda x: consolidate_pitch(PITCH_NAMES.get(x, x))
    )
    _PITCHER_RAW_DF_CACHE[player_id] = df

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
        _BATTER_RAW_DF_CACHE[player_id] = None
        return None, None

    df = df[df["pitch_type"].notna()]
    df = df[df["pitch_type"].str.strip() != ""]
    df = df[~df["pitch_type"].isin(NON_PITCHES)]
    df["pitch_name"] = df["pitch_type"].map(
        lambda x: consolidate_pitch(PITCH_NAMES.get(x, x))
    )
    _BATTER_RAW_DF_CACHE[player_id] = df

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

    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return None

    stats_list = data.get("stats", [])

    # This happens for debut pitchers / pitchers with no MLB season stats yet.
    if not stats_list:
        return None

    splits = stats_list[0].get("splits", [])

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


def get_head_to_head_stats(batter_id, pitcher_id):
    """Career batter-vs-pitcher BA and AB count from Statcast, blended only when useful."""
    statcast_first_year = 2015
    current_year = datetime.now().year

    ab_events = {
        "single",
        "double",
        "triple",
        "home_run",
        "strikeout",
        "field_out",
        "grounded_into_double_play",
        "double_play",
        "triple_play",
        "fielders_choice",
        "fielders_choice_out",
        "force_out",
        "other_out",
        "strikeout_double_play",
        "field_error",
    }
    hit_events = {"single", "double", "triple", "home_run"}

    total_ab = 0
    total_hits = 0

    for year in range(statcast_first_year, current_year + 1):
        try:
            df = statcast_batter(f"{year}-03-01", f"{year}-11-30", batter_id)

            if df is None or df.empty:
                continue

            matchup = df[df["pitcher"] == pitcher_id]

            if matchup.empty:
                continue

            pa_endings = matchup[matchup["events"].notna()]
            ab_pa = pa_endings[pa_endings["events"].isin(ab_events)]
            hit_pa = pa_endings[pa_endings["events"].isin(hit_events)]

            total_ab += len(ab_pa)
            total_hits += len(hit_pa)

        except Exception:
            continue

    if total_ab == 0:
        return None, 0

    return round(total_hits / total_ab, 4), total_ab


def blend_matchup_ba(matchup_xba, h2h_ba, h2h_ab, credibility_k=H2H_CREDIBILITY_K):
    """Blend matchup xBA with career H2H BA, requiring >5 ABs and capping weight at 20%."""
    if matchup_xba is None:
        return matchup_xba, 0, None

    if h2h_ba is None or h2h_ab == 0 or h2h_ab <= 5:
        return matchup_xba, 0, None

    raw_weight = h2h_ab / (h2h_ab + credibility_k)
    h2h_weight = min(raw_weight, 0.20)
    blended = round((1 - h2h_weight) * matchup_xba + h2h_weight * h2h_ba, 4)

    return blended, h2h_ab, h2h_weight


@st.cache_data(show_spinner=False)
def get_sprint_speed(player_id):
    """Fetch sprint speed from the MLB Stats API."""
    current_year = datetime.now().year
    url = (
        f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
        f"?stats=sprintSpeed&season={current_year}&group=hitting"
    )

    response = requests.get(url, timeout=20)
    data = response.json()
    splits = data.get("stats", [{}])[0].get("splits", [])

    if not splits:
        return None

    speed = splits[0].get("stat", {}).get("sprintSpeed")

    try:
        return float(speed) if speed is not None else None
    except (TypeError, ValueError):
        return None


def compute_speed_adjustment(
    sprint_speed,
    league_avg=LEAGUE_AVG_SPRINT_SPEED,
    scalar=SPEED_BABIP_SCALAR,
    cap=SPEED_ADJ_CAP,
):
    """Additive BA delta from sprint speed, capped to keep it small."""
    if sprint_speed is None:
        return 0.0, None

    raw_adj = (sprint_speed - league_avg) * scalar
    adj = round(max(-cap, min(cap, raw_adj)), 4)

    if sprint_speed >= 28.5:
        tier = "elite speed"
    elif sprint_speed >= 27.5:
        tier = "above average"
    elif sprint_speed >= 26.5:
        tier = "average"
    elif sprint_speed >= 25.5:
        tier = "below average"
    else:
        tier = "slow"

    return adj, f"{sprint_speed:.1f} ft/sec ({tier})"


def compute_hit_probabilities(matchup_slg, hit_breakdown):
    if matchup_slg is None or hit_breakdown is None:
        return None

    tb_weights = {"singles": 1, "doubles": 2, "triples": 3, "home_runs": 4}

    return {
        hit_type: round(matchup_slg * (hit_breakdown[hit_type]["pct"] / 100) / tb_val, 4)
        for hit_type, tb_val in tb_weights.items()
    }


def compute_hit_type_probabilities(matchup_ba, hit_breakdown):
    if matchup_ba is None or hit_breakdown is None:
        return None

    total_hits = sum(
        hit_breakdown[k]["count"]
        for k in ["singles", "doubles", "triples", "home_runs"]
    )

    if total_hits == 0:
        return None

    return {
        hit_type: round(matchup_ba * hit_breakdown[hit_type]["count"] / total_hits, 4)
        for hit_type in ["singles", "doubles", "triples", "home_runs"]
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
    """
    matchup xBA/xSLG are BIP metrics, so K-rate is applied to matchup ABs only.
    Season AVG/SLG are observed stats with strikeouts already reflected.
    """
    results = {}

    def to_ml(prob):
        if prob is None or prob <= 0:
            return "N/A"

        if prob >= 1.0:
            return "+∞"

        if prob > 0.5:
            return f"-{round((prob / (1 - prob)) * 100)}"

        return f"+{round(((1 - prob) / prob) * 100)}"

    total_abs = ab_per_game if ab_per_game is not None else 3.5
    matchup_abs = min(2.5, total_abs)
    season_abs = max(0.0, total_abs - matchup_abs)
    kr = k_rate_per_ab if k_rate_per_ab is not None else 0.0

    m_ba = matchup_ba * (1 - kr) if matchup_ba is not None else None
    m_slg = matchup_slg * (1 - kr) if matchup_slg is not None else None
    s_ba = season_avg_f
    s_slg = season_slg_f

    if m_ba is not None and s_ba is not None:
        p_no_hit = ((1 - m_ba) ** matchup_abs) * ((1 - s_ba) ** season_abs)
        results["1_hit"] = {"prob": round(1 - p_no_hit, 4)}
    else:
        results["1_hit"] = {"prob": None}

    if m_ba is not None and s_ba is not None and total_abs > 0:
        eff_ba = (m_ba * matchup_abs + s_ba * season_abs) / total_abs
        p0 = (1 - eff_ba) ** total_abs
        p1 = total_abs * eff_ba * ((1 - eff_ba) ** (total_abs - 1))
        results["2_hits"] = {"prob": round(1 - p0 - p1, 4)}
        _p0, _p1, _eff_ba = p0, p1, eff_ba
    else:
        results["2_hits"] = {"prob": None}
        _p0 = _p1 = _eff_ba = None

    if _p0 is not None and total_abs >= 2:
        p2 = comb(int(total_abs), 2) * (_eff_ba**2) * ((1 - _eff_ba) ** (total_abs - 2))
        results["3_hits"] = {"prob": round(1 - _p0 - _p1 - p2, 4)}
    else:
        results["3_hits"] = {"prob": None}

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
            mm_hr = blend(m_ba * hr_share, m_slg * hr_tb_pct / 4)

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
            ss_hr = blend(s_ba * hr_share, s_slg * hr_tb_pct / 4)

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

            results["2_tb"] = {"prob": round(1 - p_no_hit - p_one_single, 4)}
        else:
            results["2_tb"] = {"prob": None}
    else:
        results["2_tb"] = {"prob": None}

    hr_prob_ba = None
    hr_prob_slg = None

    if hit_breakdown and m_ba is not None and s_ba is not None:
        total_hits = sum(
            hit_breakdown[k]["count"]
            for k in ["singles", "doubles", "triples", "home_runs"]
        )

        if total_hits > 0:
            hr_share = hit_breakdown["home_runs"]["count"] / total_hits
            p_no_hr = ((1 - m_ba * hr_share) ** matchup_abs) * (
                (1 - s_ba * hr_share) ** season_abs
            )
            hr_prob_ba = round(1 - p_no_hr, 4)

    if hit_breakdown and m_slg is not None and s_slg is not None:
        hr_tb_pct = hit_breakdown["home_runs"]["pct"] / 100
        p_no_hr = ((1 - m_slg * hr_tb_pct / 4) ** matchup_abs) * (
            (1 - s_slg * hr_tb_pct / 4) ** season_abs
        )
        hr_prob_slg = round(1 - p_no_hr, 4)

    if hr_prob_ba is not None and hr_prob_slg is not None:
        hr_final = round((hr_prob_ba + hr_prob_slg) / 2, 4)
    elif hr_prob_ba is not None:
        hr_final = hr_prob_ba
    else:
        hr_final = hr_prob_slg

    results["hr"] = {"prob": hr_final}

    for key in results:
        results[key]["ml"] = to_ml(results[key]["prob"])

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
            "1 Hit ML": "N/A",
            "Prob 2 hits": "N/A",
            "2 Hit ML": "N/A",
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

    pitcher_df = _PITCHER_RAW_DF_CACHE.get(pitcher_id)
    hitter_df = _BATTER_RAW_DF_CACHE.get(hitter_id)
    league_df = _LEAGUE_RAW_DF_CACHE

    zone_scalar = None

    if pitcher_df is not None and hitter_df is not None and league_df is not None:
        pitcher_zone_profile = get_pitcher_zone_profile_by_pitch(pitcher_df)
        hitter_zone_profile = get_hitter_zone_profile_by_pitch(hitter_df)
        league_zone_avgs = get_league_zone_averages_by_pitch(league_df)

        zone_scalar, _ = compute_zone_scalar_by_pitch(
            pitcher_zone_profile,
            hitter_zone_profile,
            league_zone_avgs,
            pitcher_relevant_split,
            effective_bat_side,
            pitch_hand,
        )

    matchup_ba_zoned = (
        round(matchup_ba * zone_scalar, 4)
        if matchup_ba is not None and zone_scalar is not None
        else matchup_ba
    )

    h2h_ba, h2h_ab = get_head_to_head_stats(hitter_id, pitcher_id)
    matchup_ba_blended, _, _ = blend_matchup_ba(matchup_ba_zoned, h2h_ba, h2h_ab)

    sprint_speed = get_sprint_speed(hitter_id)
    speed_adj, _ = compute_speed_adjustment(sprint_speed)

    matchup_ba_final = (
        round(matchup_ba_blended + speed_adj, 4)
        if matchup_ba_blended is not None
        else None
    )

    # Half-weight speed nudge to season AVG because observed average already partially includes speed.
    if season_avg_f is not None:
        season_avg_f = round(season_avg_f + speed_adj * 0.5, 4)

    hit_breakdown = get_batter_hit_breakdown(hitter_id)

    betting_odds = compute_betting_odds(
        matchup_ba_final,
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