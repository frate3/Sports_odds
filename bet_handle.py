from pybaseball import playerid_lookup, statcast_batter
import sqlite3
from datetime import datetime

DB_PATH = "data/history.db"
# key = "88922fff-a6a7-4394-96c1-a7430fccb56d"

def get_db():
    conn = sqlite3.connect(
        DB_PATH,
        timeout=10,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

# -----------------------------
# Write DB
# -----------------------------

def normalize_odds(odds):
    odds_text = str(odds).strip()

    if not odds_text:
        raise ValueError("Odds cannot be blank.")

    if odds_text[0] not in ["+", "-"]:
        odds_text = f"+{odds_text}"

    try:
        int(odds_text[1:])
    except ValueError as exc:
        raise ValueError("Odds must look like +120 or -150.") from exc

    return odds_text


def create_entry(name, line, odds, wager):

    line_value = float(line)
    wager_value = float(wager)
    odds_text = normalize_odds(odds)

    if line_value < 0:
        raise ValueError("Line cannot be negative.")

    if wager_value <= 0:
        raise ValueError("Wager must be greater than 0.")

    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO history (
            Date,
            Name,
            Line,
            Odds,
            Wager
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            datetime.now().strftime("%Y-%m-%d"),
            name,
            line_value,
            odds_text,
            wager_value,
        ),
    )

    conn.commit()
    conn.close()

    return {"status": "ok"}


# -----------------------------
# Fill DB
# -----------------------------
def search_db(only_open=True):

    conn = get_db()
    cur = conn.cursor()

    if only_open:
        cur.execute(
            """
            SELECT *
            FROM history
            WHERE Result IS NULL
            ORDER BY Date DESC, ID DESC
            """
        )
    else:
        cur.execute(
            """
            SELECT *
            FROM history
            ORDER BY Date DESC, ID DESC
            """
        )

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows

def write_to_db(id,result,payout):
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE history SET
            Result = ?,
            Payout = ?
        WHERE ID = ?
    """, (
        result,
        payout,
        id
    ))

    conn.commit()
    conn.close()
    return {"status": "ok"}

def get_abs(first,last,date):
    try:
        pid = playerid_lookup(last, first)["key_mlbam"].iloc[0]
    except:
        print("player not found")
    stats = statcast_batter(date, date, int(pid))
    hit_types = ["single",'double','triple','home_run']
    hits = 0
    outs = 0
    for pitch in stats.events:
        if (pitch in hit_types):
            # print(pitch)
            hits +=1
        if ("out" in str(pitch)):
            outs +=1
    return f"{hits}/{hits+outs}"

def calc_payout(odds,wager):
    odds_text = normalize_odds(odds)
    num_odds = int(odds_text[1:])
    wager_value = float(wager)

    if odds_text[0] == "-":
        return round(wager_value + (wager_value * 100 / num_odds), 2)

    return round(wager_value + (wager_value * num_odds / 100), 2)

def fill_blanks():
    lines = search_db(only_open=True)
    for player in lines:
        #fetch abs
        first, last = player['Name'].split()
        if "." in first:
            l1, l2 = first.split(".",1)
            first = f"{l1}. {l2}"
        game_ab = get_abs(first,last,player['Date'])
        if game_ab != "0/0":
            #compare to line
            

            if float(player["Line"])<=int(game_ab[0]) and float(player["Line"]) !=0:
                #calc payout
                payout = calc_payout(player["Odds"],float(player["Wager"]))
            elif float(player["Line"]) == 0 and float(player["Line"])>=int(game_ab[0]):
                payout = calc_payout(player["Odds"],float(player["Wager"]))
            else:
                payout = 0
            #write to file
            write_to_db(player["ID"],game_ab,payout)
            # return f"result: {game_ab},{payout}"

