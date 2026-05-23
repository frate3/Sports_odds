from pybaseball import statcast
from kalshi_handle import get_kalshi_hit_odds


def american_to_probability(odds):
    odds = int(odds)
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)

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


print(get_kalshi_hit_percents("Munetaka Murakami"))
