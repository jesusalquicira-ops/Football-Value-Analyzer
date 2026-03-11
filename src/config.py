# ============================================================
#  config.py — Competiciones y constantes del modelo
# ============================================================

# Mapeo de ligas con IDs para ambas APIs
# football-data.org codes: https://www.football-data.org/coverage
# the-odds-api keys: https://the-odds-api.com/sports/
LEAGUES = {
    "UEFA Champions League": {
        "fd_code":    "CL",
        "odds_key":   "soccer_uefa_champs_league",
        "flag":       "🏆",
    },
    "UEFA Europa League": {
        "fd_code":    "EL",
        "odds_key":   "soccer_uefa_europa_league",
        "flag":       "🟠",
    },
    "Premier League": {
        "fd_code":    "PL",
        "odds_key":   "soccer_epl",
        "flag":       "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    },
    "La Liga": {
        "fd_code":    "PD",
        "odds_key":   "soccer_spain_la_liga",
        "flag":       "🇪🇸",
    },
    "Bundesliga": {
        "fd_code":    "BL1",
        "odds_key":   "soccer_germany_bundesliga",
        "flag":       "🇩🇪",
    },
    "Serie A": {
        "fd_code":    "SA",
        "odds_key":   "soccer_italy_serie_a",
        "flag":       "🇮🇹",
    },
    "Ligue 1": {
        "fd_code":    "FL1",
        "odds_key":   "soccer_france_ligue_one",
        "flag":       "🇫🇷",
    },
    "Liga MX": {
        "fd_code":    None,               # No cubierta por football-data.org free
        "odds_key":   "soccer_mexico_ligamx",
        "flag":       "🇲🇽",
    },
}

# Modelo
DC_RHO          = -0.13   # Corrección Dixon-Coles
MAX_GOALS       = 10      # Máximo goles simulados en matriz
VALUE_THRESHOLD = 0.05    # 5% de ventaja mínima para considerar "valor"
HOME_ADVANTAGE  = 1.10    # Factor local por defecto

# Casas preferidas (menor margen primero)
PREFERRED_BOOKS = [
    "pinnacle", "betfair_ex_eu", "betfair_ex_uk",
    "bet365", "williamhill", "unibet_eu",
]

# Cache TTLs (segundos)
CACHE_TTL_FIXTURES = 1800       # 30 min
CACHE_TTL_STATS    = 3600 * 6   # 6 horas
CACHE_TTL_ODDS     = 60 * 10    # 10 min
