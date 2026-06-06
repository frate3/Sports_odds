import requests
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd


BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
HITS_SERIES = "KXMLBHIT"


# -------------------------------------------------
# General Helpers
# -------------------------------------------------

def get_json(url, params=None):
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def round_half_up(value):
    """
    Normal Python round() uses banker's rounding.
    This rounds .5 upward, which is usually better for displayed odds.
    """
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_odds(odds):
    """
    Converts integer American odds into display text.
    Example:
        -209 -> "-209"
        172 -> "+172"
    """
    if odds is None:
        return None

    if odds > 0:
        return f"+{odds}"

    return str(odds)


# -------------------------------------------------
# Odds Helpers
# -------------------------------------------------

def raw_american_odds(prob):
    """
    Converts probability/contract price into American odds.
    This does NOT include Kalshi fees.

    Example:
        0.66 -> -194
        0.35 -> +186
    """
    if prob is None or prob <= 0 or prob >= 1:
        return None

    prob = Decimal(str(prob))

    if prob >= Decimal("0.5"):
        odds = -100 * prob / (Decimal("1") - prob)
    else:
        odds = 100 * (Decimal("1") - prob) / prob

    return round_half_up(odds)


def kalshi_fee(price):
    """
    Approximate Kalshi taker fee per contract.

    Formula:
        fee = 0.07 * price * (1 - price)

    price should be:
        0.66 for 66 cents
    """
    if price is None or price <= 0 or price >= 1:
        return None

    price = Decimal(str(price))
    return Decimal("0.07") * price * (Decimal("1") - price)


def kalshi_display_odds(price):
    """
    Converts Kalshi buy price into American odds INCLUDING Kalshi fee.

    This is the number that should be closer to Kalshi's displayed odds.

    For buying a contract:
        risk = price + fee
        profit = 1 - price - fee
    """
    if price is None or price <= 0 or price >= 1:
        return None

    price = Decimal(str(price))
    fee = kalshi_fee(price)

    if fee is None:
        return None

    risk = price + fee
    profit = Decimal("1") - price - fee

    if profit <= 0:
        return None

    if risk >= profit:
        odds = -100 * risk / profit
    else:
        odds = 100 * profit / risk

    return round_half_up(odds)


# -------------------------------------------------
# Kalshi API Fetching
# -------------------------------------------------

def get_all_open_hit_markets(limit=1000):
    """
    Gets all open Kalshi MLB hits markets.
    """
    markets = []
    cursor = None

    while True:
        params = {
            "series_ticker": HITS_SERIES,
            "status": "open",
            "limit": limit,
        }

        if cursor:
            params["cursor"] = cursor

        data = get_json(f"{BASE_URL}/markets", params=params)
        markets.extend(data.get("markets", []))

        cursor = data.get("cursor")
        if not cursor:
            break

    return markets


def get_orderbook(ticker):
    """
    Gets the Kalshi orderbook for one market.

    Kalshi orderbook gives:
        yes_dollars = YES bids
        no_dollars = NO bids

    It does not directly give American odds.
    """
    data = get_json(f"{BASE_URL}/markets/{ticker}/orderbook")
    return data.get("orderbook_fp", {})


def get_best_price(price_levels):
    """
    Kalshi price levels usually look like:
        [["0.6500", 123], ["0.6600", 50]]

    The last level is treated as the best price.
    """
    if not price_levels:
        return None

    try:
        return Decimal(str(price_levels[-1][0]))
    except Exception:
        return None


def best_yes_no_prices(orderbook):
    """
    Returns both YES and NO buy/sell prices.

    Best YES bid:
        highest YES bid

    Best YES ask:
        1.00 - best NO bid

    Best NO bid:
        highest NO bid

    Best NO ask:
        1.00 - best YES bid
    """
    yes_bids = orderbook.get("yes_dollars", [])
    no_bids = orderbook.get("no_dollars", [])

    best_yes_bid = get_best_price(yes_bids)
    best_no_bid = get_best_price(no_bids)

    best_yes_ask = (
        Decimal("1.00") - best_no_bid
        if best_no_bid is not None
        else None
    )

    best_no_ask = (
        Decimal("1.00") - best_yes_bid
        if best_yes_bid is not None
        else None
    )

    if best_yes_bid is not None and best_yes_ask is not None:
        yes_mid = (best_yes_bid + best_yes_ask) / Decimal("2")
    elif best_yes_bid is not None:
        yes_mid = best_yes_bid
    elif best_yes_ask is not None:
        yes_mid = best_yes_ask
    else:
        yes_mid = None

    if best_no_bid is not None and best_no_ask is not None:
        no_mid = (best_no_bid + best_no_ask) / Decimal("2")
    elif best_no_bid is not None:
        no_mid = best_no_bid
    elif best_no_ask is not None:
        no_mid = best_no_ask
    else:
        no_mid = None

    return {
        "yes_bid": float(best_yes_bid) if best_yes_bid is not None else None,
        "yes_ask": float(best_yes_ask) if best_yes_ask is not None else None,
        "yes_mid": float(yes_mid) if yes_mid is not None else None,

        "no_bid": float(best_no_bid) if best_no_bid is not None else None,
        "no_ask": float(best_no_ask) if best_no_ask is not None else None,
        "no_mid": float(no_mid) if no_mid is not None else None,
    }


# -------------------------------------------------
# Market Matching
# -------------------------------------------------

def market_text(market):
    """
    Combines useful text fields so matching works even if Kalshi changes
    where the player name appears.
    """
    fields = [
        market.get("ticker", ""),
        market.get("title", ""),
        market.get("subtitle", ""),
        market.get("yes_sub_title", ""),
        market.get("event_ticker", ""),
        market.get("rules_primary", ""),
    ]

    return " ".join(str(x) for x in fields if x).lower()


def normalize_text(value):
    return " ".join(str(value).lower().strip().split())


def find_player_hit_markets(player_name, game_hint=None):
    """
    Finds all open hit markets for a player.

    Examples:
        player_name = "Steven Kwan"
        game_hint = "CLEDET" or "Guardians"
    """
    markets = get_all_open_hit_markets()

    player_lower = normalize_text(player_name)
    hint_lower = normalize_text(game_hint) if game_hint else None

    matched = []

    for market in markets:
        text = market_text(market)

        if player_lower not in text:
            continue

        if hint_lower and hint_lower not in text:
            continue

        matched.append(market)

    return matched


def classify_hit_line(market):
    """
    Classifies only 1+ hits and 2+ hits.
    3+ hits is intentionally ignored.
    """
    text = market_text(market)
    ticker = str(market.get("ticker", "")).upper()

    if "1+ hits" in text or "1 or more" in text or "at least 1" in text:
        return "1+ hits"

    if "2+ hits" in text or "2 or more" in text or "at least 2" in text:
        return "2+ hits"

    # Backup ticker-based classification.
    # Your example tickers end with -1 and -2.
    if ticker.endswith("-1"):
        return "1+ hits"

    if ticker.endswith("-2"):
        return "2+ hits"

    return "unknown"


def line_sort_value(line):
    if line == "0 hits":
        return 0
    if line == "1+ hits":
        return 1
    if line == "2+ hits":
        return 2
    return 99


# -------------------------------------------------
# Row Builders
# -------------------------------------------------

def build_yes_row(player_name, market, line, prices):
    """
    Builds row for buying YES on 1+ hits or 2+ hits.
    """
    yes_ask = prices["yes_ask"]
    yes_mid = prices["yes_mid"]

    raw_odds = raw_american_odds(yes_ask)
    kalshi_odds = kalshi_display_odds(yes_ask)

    return {
        "player": player_name,
        "line": line,
        "side": "YES",
        "ticker": market["ticker"],
        "market_title": market.get("title"),

        "bid": prices["yes_bid"],
        "ask": yes_ask,
        "mid_prob": yes_mid,
        "mid_percent": round(yes_mid * 100, 2) if yes_mid is not None else None,

        "raw_american_odds": raw_odds,
        "raw_american_odds_text": format_odds(raw_odds),

        "kalshi_american_odds": kalshi_odds,
        "kalshi_american_odds_text": format_odds(kalshi_odds),
    }


def build_zero_hits_row(player_name, market, prices):
    """
    Builds 0 hits row from the NO side of the 1+ hits market.

    0 hits = NO on 1+ hits.

    For buying NO:
        no_ask = 1 - yes_bid
    """
    no_ask = prices["no_ask"]
    no_mid = prices["no_mid"]

    raw_odds = raw_american_odds(no_ask)
    kalshi_odds = kalshi_display_odds(no_ask)

    return {
        "player": player_name,
        "line": "0 hits",
        "side": "NO",
        "ticker": market["ticker"],
        "market_title": market.get("title"),

        "bid": prices["no_bid"],
        "ask": no_ask,
        "mid_prob": no_mid,
        "mid_percent": round(no_mid * 100, 2) if no_mid is not None else None,

        "raw_american_odds": raw_odds,
        "raw_american_odds_text": format_odds(raw_odds),

        "kalshi_american_odds": kalshi_odds,
        "kalshi_american_odds_text": format_odds(kalshi_odds),
    }


# -------------------------------------------------
# Main Function
# -------------------------------------------------

def get_kalshi_hit_odds(player_name, game_hint=None):
    """
    Returns a dataframe containing:
        0 hits   = NO side of 1+ hits
        1+ hits  = YES side of 1+ hits
        2+ hits  = YES side of 2+ hits

    3+ hits is removed.
    """
    markets = find_player_hit_markets(player_name, game_hint)

    rows = []

    for market in markets:
        line = classify_hit_line(market)

        if line not in ["1+ hits", "2+ hits"]:
            continue

        ticker = market["ticker"]
        orderbook = get_orderbook(ticker)
        prices = best_yes_no_prices(orderbook)

        # Add 0 hits from the NO side of the 1+ hits market.
        if line == "1+ hits":
            rows.append(build_zero_hits_row(player_name, market, prices))

        # Add normal YES rows for 1+ hits and 2+ hits.
        rows.append(build_yes_row(player_name, market, line, prices))

    df = pd.DataFrame(rows)

    if not df.empty:
        df["line_sort"] = df["line"].apply(line_sort_value)
        df = df.sort_values(["line_sort", "market_title"])
        df = df.drop(columns=["line_sort"])
        df = df.reset_index(drop=True)

    return df["kalshi_american_odds_text"]


# -------------------------------------------------
# Terminal Test
# -------------------------------------------------

if __name__ == "__main__":
    player = input("Player name: ").strip()

    # game_hint = input("Game hint optional, like CLEDET or Guardians: ").strip()
    # game_hint = game_hint if game_hint else None

    df = get_kalshi_hit_odds(player)

    if df.empty:
        print("No 0, 1+, or 2+ hit Kalshi markets found for that player.")
    else:
        print(df.to_string(index=False))