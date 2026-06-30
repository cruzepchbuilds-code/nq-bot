# dashboard/server.py
# Lightweight web server for the live trading dashboard.
# Serves dashboard/index.html and provides JSON API endpoints for:
#   GET /api/status   — current position, P&L, Apex floor
#   GET /api/trades   — today's trades
#   GET /api/account  — account balance, DD remaining
#   GET /api/stats    — rolling win rate, profit factor (last 30 trades)
#
# Usage (when implemented):
#   python dashboard/server.py
#   Then open http://localhost:8080 in a browser.
#
# Not yet implemented.
