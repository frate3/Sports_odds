def american_to_probability(odds):
    odds = int(odds)
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)

while True:
    odds = input("Enter Odds: ")
    print(american_to_probability(odds))
