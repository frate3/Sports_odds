from pybaseball import statcast

df = statcast("2026-05-20", "2026-05-20")
soto = df[df["batter"] == 665742]
print(soto)