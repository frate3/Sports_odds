@echo off
cd /d "C:\Users\Admin\Documents\.projects\Sports_odds"

python precompute_matchups.py 
python -m streamlit run gui.py

pause