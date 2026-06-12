def check_arbitrage(percent_a, percent_b, total_wager=100):
    """
    percent_a: implied probability for side A, like 48.5
    percent_b: implied probability for side B, like 49.0
    total_wager: total amount you want to risk across both sides

    Works when side A and side B are opposite sides of the same line.
    Example:
        App A: Over 1.5 hits = 48%
        App B: Under 1.5 hits = 49%
    """

    p_a = percent_a / 100
    p_b = percent_b / 100

    total_implied = p_a + p_b

    print("-" * 40)
    print(f"Side A implied probability: {percent_a:.2f}%")
    print(f"Side B implied probability: {percent_b:.2f}%")
    print(f"Total implied probability: {total_implied * 100:.2f}%")

    if total_implied >= 1:
        print("\nNo guaranteed profit.")
        print(f"Overround: {(total_implied - 1) * 100:.2f}%")
        return None

    profit_margin = 1 - total_implied

    # Decimal odds from implied probability
    decimal_a = 1 / p_a
    decimal_b = 1 / p_b

    # Stake split to make payout equal on both sides
    stake_a = total_wager * p_a / total_implied
    stake_b = total_wager * p_b / total_implied

    payout_a = stake_a * decimal_a
    payout_b = stake_b * decimal_b

    guaranteed_payout = min(payout_a, payout_b)
    guaranteed_profit = guaranteed_payout - total_wager
    roi = guaranteed_profit / total_wager * 100

    print("\nArbitrage found.")
    print(f"Profit margin: {profit_margin * 100:.2f}%")

    print("\nRecommended wager split:")
    print(f"Bet ${stake_a:.2f} on Side A")
    print(f"Bet ${stake_b:.2f} on Side B")

    print("\nResult:")
    print(f"Guaranteed payout: ${guaranteed_payout:.2f}")
    print(f"Guaranteed profit: ${guaranteed_profit:.2f}")
    print(f"ROI: {roi:.2f}%")

    return {
        "arbitrage": True,
        "total_implied_percent": round(total_implied * 100, 2),
        "profit_margin_percent": round(profit_margin * 100, 2),
        "stake_a": round(stake_a, 2),
        "stake_b": round(stake_b, 2),
        "guaranteed_payout": round(guaranteed_payout, 2),
        "guaranteed_profit": round(guaranteed_profit, 2),
        "roi_percent": round(roi, 2),
    }


if __name__ == "__main__":
    print("Sports Arbitrage Checker")
    print("Enter the implied percentages for opposite sides of the same line.")
    print("Example: Over = 48, Under = 49\n")

    percent_a = float(input("Side A percentage: "))
    percent_b = float(input("Side B percentage: "))

    wager_input = input("Total wager amount default 100: ").strip()

    if wager_input:
        total_wager = float(wager_input)
    else:
        total_wager = 100

    check_arbitrage(percent_a, percent_b, total_wager)