"""
baseball_moneylines.py

Standalone script to pull MLB game moneyline-style prices from Kalshi and Polymarket.

Usage examples:
    python baseball_moneylines.py "Yankees" "Red Sox"
    python baseball_moneylines.py "New York Yankees" "Boston Red Sox" --total-wager 100

Notes:
- Kalshi game-winner markets are usually in series KXMLBGAME.
- Kalshi is binary: YES = one listed team wins, NO = the other listed team wins.
- Polymarket outcomes may be two-way or occasionally include extra outcomes like Draw for other sports.
  This script filters to the two team outcomes you entered.
- Prices are converted to American odds using the current available buy price when possible.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

import requests


KALSHI_BASE_URL = "https://external-api.kalshi.com/trade-api/v2"
KALSHI_MLB_GAME_SERIES = "KXMLBGAME"

POLYMARKET_GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

REQUEST_TIMEOUT = 15


# Kalshi often uses shorter team names in market titles, so aliases help matching.
TEAM_ALIASES = {
    "arizona diamondbacks": ["arizona", "diamondbacks", "ari"],
    "atlanta braves": ["atlanta", "braves", "atl"],
    "baltimore orioles": ["baltimore", "orioles", "bal"],
    "boston red sox": ["boston", "red sox", "bos"],
    "chicago cubs": ["chicago c", "cubs", "chc"],
    "chicago white sox": ["chicago w", "white sox", "chw", "cws"],
    "cincinnati reds": ["cincinnati", "reds", "cin"],
    "cleveland guardians": ["cleveland", "guardians", "cle"],
    "colorado rockies": ["colorado", "rockies", "col"],
    "detroit tigers": ["detroit", "tigers", "det"],
    "houston astros": ["houston", "astros", "hou"],
    "kansas city royals": ["kansas city", "royals", "kc"],
    "los angeles angels": ["los angeles a", "angels", "laa"],
    "los angeles dodgers": ["los angeles d", "dodgers", "lad"],
    "miami marlins": ["miami", "marlins", "mia"],
    "milwaukee brewers": ["milwaukee", "brewers", "mil"],
    "minnesota twins": ["minnesota", "twins", "min"],
    "new york mets": ["new york m", "mets", "nym"],
    "new york yankees": ["new york y", "yankees", "nyy"],
    "athletics": ["athletics", "ath", "oakland", "oak"],
    "oakland athletics": ["athletics", "ath", "oakland", "oak"],
    "philadelphia phillies": ["philadelphia", "phillies", "phi"],
    "pittsburgh pirates": ["pittsburgh", "pirates", "pit"],
    "san diego padres": ["san diego", "padres", "sd"],
    "san francisco giants": ["san francisco", "giants", "sf"],
    "seattle mariners": ["seattle", "mariners", "sea"],
    "st. louis cardinals": ["st. louis", "st louis", "cardinals", "stl"],
    "tampa bay rays": ["tampa bay", "rays", "tb"],
    "texas rangers": ["texas", "rangers", "tex"],
    "toronto blue jays": ["toronto", "blue jays", "tor"],
    "washington nationals": ["washington", "nationals", "was", "wsh"],
}


@dataclass
class MoneylineRow:
    app: str
    market: str
    team: str
    side: str
    price: Optional[float]
    implied_percent: Optional[float]
    american_odds: Optional[str]
    url_or_ticker: str


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).lower().replace(".", "")).strip()


def aliases_for(team: str) -> list[str]:
    key = normalize_text(team)

    aliases = {key, team.lower().strip()}

    # Direct full-team alias match.
    if key in TEAM_ALIASES:
        aliases.update(TEAM_ALIASES[key])

    # Also allow partial matching by city/nickname if user enters only "Yankees" or "Mets".
    for full_name, vals in TEAM_ALIASES.items():
        all_names = {full_name, *vals}
        if key in {normalize_text(x) for x in all_names}:
            aliases.add(full_name)
            aliases.update(vals)

    return sorted({normalize_text(a) for a in aliases if a})


def text_contains_any(text: str, aliases: list[str]) -> bool:
    text = normalize_text(text)
    return any(alias in text for alias in aliases)


def request_json(url: str, params: Optional[dict[str, Any]] = None) -> Any:
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def probability_to_american(prob: Optional[float]) -> Optional[str]:
    if prob is None or prob <= 0 or prob >= 1:
        return None

    p = Decimal(str(prob))

    if p >= Decimal("0.5"):
        odds = -100 * p / (Decimal("1") - p)
    else:
        odds = 100 * (Decimal("1") - p) / p

    rounded = round_half_up(odds)
    return f"+{rounded}" if rounded > 0 else str(rounded)


def kalshi_fee(price: Optional[float]) -> Optional[float]:
    """Approximate Kalshi taker fee: 7% * price * (1 - price)."""
    if price is None or price <= 0 or price >= 1:
        return None
    p = Decimal(str(price))
    return float(Decimal("0.07") * p * (Decimal("1") - p))


def kalshi_buy_price_to_odds(price: Optional[float], include_fee: bool = True) -> tuple[Optional[float], Optional[str]]:
    """
    Convert a Kalshi buy price into implied percent and American odds.
    If include_fee=True, odds use approximate fee-adjusted risk/profit.
    """
    if price is None or price <= 0 or price >= 1:
        return None, None

    if not include_fee:
        return round(price * 100, 2), probability_to_american(price)

    fee = kalshi_fee(price)
    if fee is None:
        return round(price * 100, 2), probability_to_american(price)

    risk = price + fee
    profit = 1 - price - fee
    if profit <= 0:
        return round(risk * 100, 2), None

    # Convert fee-adjusted risk/profit into American odds.
    if risk >= profit:
        odds = -100 * Decimal(str(risk)) / Decimal(str(profit))
    else:
        odds = 100 * Decimal(str(profit)) / Decimal(str(risk))

    rounded = round_half_up(odds)
    return round(risk * 100, 2), f"+{rounded}" if rounded > 0 else str(rounded)


def get_best_price(levels: Any) -> Optional[float]:
    """
    Kalshi orderbook levels usually look like [["0.6500", quantity], ...].
    The last level is usually the best price.
    """
    if not levels:
        return None
    try:
        return float(levels[-1][0])
    except Exception:
        return None


def kalshi_orderbook_prices(ticker: str) -> dict[str, Optional[float]]:
    data = request_json(f"{KALSHI_BASE_URL}/markets/{ticker}/orderbook")
    orderbook = data.get("orderbook_fp", {})

    yes_bid = get_best_price(orderbook.get("yes_dollars"))
    no_bid = get_best_price(orderbook.get("no_dollars"))

    # To buy YES, you pay the best YES ask, which is 1 - best NO bid.
    # To buy NO, you pay the best NO ask, which is 1 - best YES bid.
    yes_ask = 1 - no_bid if no_bid is not None else None
    no_ask = 1 - yes_bid if yes_bid is not None else None

    return {
        "yes_bid": yes_bid,
        "yes_ask": yes_ask,
        "no_bid": no_bid,
        "no_ask": no_ask,
    }


def kalshi_market_text(market: dict[str, Any]) -> str:
    fields = [
        market.get("ticker", ""),
        market.get("event_ticker", ""),
        market.get("title", ""),
        market.get("subtitle", ""),
        market.get("yes_sub_title", ""),
        market.get("rules_primary", ""),
    ]
    return " ".join(str(x) for x in fields if x)


def get_all_kalshi_game_markets(limit: int = 1000) -> list[dict[str, Any]]:
    markets: list[dict[str, Any]] = []
    cursor = None

    while True:
        params = {
            "series_ticker": KALSHI_MLB_GAME_SERIES,
            "status": "open",
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor

        data = request_json(f"{KALSHI_BASE_URL}/markets", params=params)
        markets.extend(data.get("markets", []))

        cursor = data.get("cursor")
        if not cursor:
            break

    return markets


def infer_kalshi_yes_team(market: dict[str, Any], team_a: str, team_b: str) -> Optional[str]:
    yes_text = " ".join(
        str(market.get(k, ""))
        for k in ["yes_sub_title", "subtitle", "rules_primary", "ticker"]
    )
    a_aliases = aliases_for(team_a)
    b_aliases = aliases_for(team_b)

    a_match = text_contains_any(yes_text, a_aliases)
    b_match = text_contains_any(yes_text, b_aliases)

    if a_match and not b_match:
        return team_a
    if b_match and not a_match:
        return team_b

    # Ticker suffix is often the YES-side abbreviation, like ...-NYY.
    ticker_suffix = str(market.get("ticker", "")).split("-")[-1]
    suffix_norm = normalize_text(ticker_suffix)

    if suffix_norm in a_aliases:
        return team_a
    if suffix_norm in b_aliases:
        return team_b

    return None


def get_kalshi_moneylines(team_a: str, team_b: str, include_fee: bool = True) -> list[MoneylineRow]:
    a_aliases = aliases_for(team_a)
    b_aliases = aliases_for(team_b)

    matched_market = None
    for market in get_all_kalshi_game_markets():
        text = kalshi_market_text(market)
        if text_contains_any(text, a_aliases) and text_contains_any(text, b_aliases):
            matched_market = market
            break

    if matched_market is None:
        return []

    ticker = matched_market["ticker"]
    title = matched_market.get("title") or ticker
    prices = kalshi_orderbook_prices(ticker)

    yes_team = infer_kalshi_yes_team(matched_market, team_a, team_b)
    if yes_team is None:
        yes_team = "YES outcome"
        no_team = "NO outcome"
    else:
        no_team = team_b if normalize_text(yes_team) == normalize_text(team_a) else team_a

    rows = []

    yes_percent, yes_odds = kalshi_buy_price_to_odds(prices["yes_ask"], include_fee=include_fee)
    rows.append(MoneylineRow(
        app="Kalshi",
        market=title,
        team=yes_team,
        side="YES",
        price=prices["yes_ask"],
        implied_percent=yes_percent,
        american_odds=yes_odds,
        url_or_ticker=ticker,
    ))

    no_percent, no_odds = kalshi_buy_price_to_odds(prices["no_ask"], include_fee=include_fee)
    rows.append(MoneylineRow(
        app="Kalshi",
        market=title,
        team=no_team,
        side="NO",
        price=prices["no_ask"],
        implied_percent=no_percent,
        american_odds=no_odds,
        url_or_ticker=ticker,
    ))

    return rows


def parse_jsonish_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def polymarket_search_markets(team_a: str, team_b: str, limit: int = 100) -> list[dict[str, Any]]:
    # Search both team names. Polymarket Gamma search can vary, so this is intentionally broad.
    queries = [
        f"{team_a} {team_b}",
        f"{team_b} {team_a}",
        team_a,
        team_b,
    ]
    
    seen_ids = set()
    markets: list[dict[str, Any]] = []

    for q in queries:
        params = {
            "closed": "false",
            "active": "true",
            "limit": limit,
            "q": q,
        }
        try:
            # data = request_json(f"{POLYMARKET_GAMMA_BASE_URL}/sports/markets-types", params=params)
            url = "https://gamma-api.polymarket.com/sports/market-types"

            data = requests.get(url)
        except Exception as e:
            print(e)
            continue
        print(data)
        if not isinstance(data, list):
            continue

        for market in data:
            market_id = market.get("id") or market.get("conditionId") or market.get("question")
            if market_id in seen_ids:
                continue
            seen_ids.add(market_id)
            markets.append(market)

    return markets


def polymarket_market_text(market: dict[str, Any]) -> str:
    fields = [
        market.get("question", ""),
        market.get("title", ""),
        market.get("slug", ""),
        market.get("description", ""),
        market.get("eventSlug", ""),
    ]
    return " ".join(str(x) for x in fields if x)


def get_polymarket_moneylines(team_a: str, team_b: str) -> list[MoneylineRow]:
    a_aliases = aliases_for(team_a)
    b_aliases = aliases_for(team_b)

    matched_market = None
    for market in polymarket_search_markets(team_a, team_b):
        text = polymarket_market_text(market)
        if not (text_contains_any(text, a_aliases) and text_contains_any(text, b_aliases)):
            continue
            
        outcomes = [normalize_text(x) for x in parse_jsonish_list(market.get("outcomes"))]
        if not outcomes:
            continue

        # Keep markets where both entered teams appear as outcomes or in the question text.
        has_a_outcome = any(text_contains_any(outcome, a_aliases) for outcome in outcomes)
        has_b_outcome = any(text_contains_any(outcome, b_aliases) for outcome in outcomes)

        if has_a_outcome and has_b_outcome:
            matched_market = market
            break

    if matched_market is None:
        return []

    question = matched_market.get("question") or matched_market.get("title") or "Polymarket market"
    outcomes = parse_jsonish_list(matched_market.get("outcomes"))
    prices = parse_jsonish_list(matched_market.get("outcomePrices"))

    rows: list[MoneylineRow] = []

    for outcome, price_value in zip(outcomes, prices):
        outcome_text = str(outcome)

        if text_contains_any(outcome_text, a_aliases):
            team = team_a
        elif text_contains_any(outcome_text, b_aliases):
            team = team_b
        else:
            continue

        try:
            price = float(price_value)
        except (TypeError, ValueError):
            price = None

        implied_percent = round(price * 100, 2) if price is not None else None
        american_odds = probability_to_american(price)

        rows.append(MoneylineRow(
            app="Polymarket",
            market=question,
            team=team,
            side="YES",
            price=price,
            implied_percent=implied_percent,
            american_odds=american_odds,
            url_or_ticker=str(matched_market.get("slug") or matched_market.get("conditionId") or ""),
        ))

    return rows


def print_rows(rows: list[MoneylineRow]) -> None:
    if not rows:
        print("No matching moneylines found.")
        return

    print()
    print(f"{'App':<12} {'Team':<24} {'Side':<6} {'Price':<8} {'Implied %':<10} {'ML':<8} Market")
    print("-" * 110)

    for row in rows:
        price = "N/A" if row.price is None else f"{row.price:.3f}"
        implied = "N/A" if row.implied_percent is None else f"{row.implied_percent:.2f}%"
        ml = row.american_odds or "N/A"
        print(f"{row.app:<12} {row.team:<24} {row.side:<6} {price:<8} {implied:<10} {ml:<8} {row.market}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Get MLB game moneyline-style prices from Kalshi and Polymarket.")
    parser.add_argument("team_a", help="First team, e.g. Yankees or New York Yankees")
    parser.add_argument("team_b", help="Second team, e.g. Red Sox or Boston Red Sox")
    parser.add_argument(
        "--no-kalshi-fee",
        action="store_true",
        help="Use raw Kalshi buy price instead of approximate fee-adjusted price.",
    )
    args = parser.parse_args()

    all_rows: list[MoneylineRow] = []

    try:
        all_rows.extend(get_kalshi_moneylines(
            args.team_a,
            args.team_b,
            include_fee=not args.no_kalshi_fee,
        ))
    except Exception as exc:
        print(f"Kalshi error: {exc}")

    try:
        all_rows.extend(get_polymarket_moneylines(args.team_a, args.team_b))
    except Exception as exc:
        print(f"Polymarket error: {exc}")

    print_rows(all_rows)


if __name__ == "__main__":
    main()
