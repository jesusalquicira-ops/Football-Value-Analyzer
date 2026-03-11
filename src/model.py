# ============================================================
#  model.py — Modelo de predicción Poisson + Dixon-Coles
# ============================================================

import numpy as np
from scipy.stats import poisson
from src.config import DC_RHO, MAX_GOALS, VALUE_THRESHOLD


def compute_lambdas(
    home_avg_scored:   float,
    home_avg_conceded: float,
    away_avg_scored:   float,
    away_avg_conceded: float,
    league_avg:        float,
    home_advantage:    float = 1.10,
) -> tuple[float, float]:
    if league_avg <= 0:
        league_avg = 2.7
    attack_home  = home_avg_scored   / league_avg
    defense_home = home_avg_conceded / league_avg
    attack_away  = away_avg_scored   / league_avg
    defense_away = away_avg_conceded / league_avg
    lh = attack_home * defense_away * league_avg * home_advantage
    la = attack_away * defense_home * league_avg
    return round(max(lh, 0.1), 4), round(max(la, 0.1), 4)


def _tau(x, y, mu, nu, rho):
    if   x == 0 and y == 0: return 1 - mu * nu * rho
    elif x == 0 and y == 1: return 1 + mu * rho
    elif x == 1 and y == 0: return 1 + nu * rho
    elif x == 1 and y == 1: return 1 - rho
    return 1.0


def build_score_matrix(lh: float, la: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    matrix = np.zeros((max_goals + 1, max_goals + 1))
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = poisson.pmf(i, lh) * poisson.pmf(j, la)
            p *= _tau(i, j, lh, la, DC_RHO)
            matrix[i][j] = max(p, 0)
    total = matrix.sum()
    if total > 0:
        matrix /= total
    return matrix


def compute_market_probs(matrix: np.ndarray) -> dict:
    n = matrix.shape[0]
    probs = {
        "home": 0.0, "draw": 0.0, "away": 0.0,
        "btts_yes": 0.0, "btts_no": 0.0,
        **{f"over_{s}": 0.0 for s in ["0_5","1_5","2_5","3_5","4_5"]},
        **{f"under_{s}": 0.0 for s in ["0_5","1_5","2_5","3_5","4_5"]},
    }
    top_scores = []
    for i in range(n):
        for j in range(n):
            p     = matrix[i][j]
            total = i + j
            top_scores.append({"score": f"{i}-{j}", "prob": float(p)})
            if i > j:    probs["home"] += p
            elif i == j: probs["draw"] += p
            else:        probs["away"] += p
            if i > 0 and j > 0: probs["btts_yes"] += p
            else:                probs["btts_no"]  += p
            for line in [0.5, 1.5, 2.5, 3.5, 4.5]:
                k = str(line).replace(".", "_")
                if total > line: probs[f"over_{k}"]  += p
                else:            probs[f"under_{k}"] += p
    top_scores.sort(key=lambda x: x["prob"], reverse=True)
    probs["top_scores"] = top_scores[:10]
    return probs


MARKET_MAP = {
    "home":      ("h2h",      "home",  "Victoria Local"),
    "draw":      ("h2h",      "draw",  "Empate"),
    "away":      ("h2h",      "away",  "Victoria Visitante"),
    "btts_yes":  ("btts",     "yes",   "Ambos anotan — SÍ"),
    "btts_no":   ("btts",     "no",    "Ambos anotan — NO"),
    "over_0_5":  ("over_0_5", "over",  "Over 0.5"),
    "under_0_5": ("over_0_5", "under", "Under 0.5"),
    "over_1_5":  ("over_1_5", "over",  "Over 1.5"),
    "under_1_5": ("over_1_5", "under", "Under 1.5"),
    "over_2_5":  ("over_2_5", "over",  "Over 2.5"),
    "under_2_5": ("over_2_5", "under", "Under 2.5"),
    "over_3_5":  ("over_3_5", "over",  "Over 3.5"),
    "under_3_5": ("over_3_5", "under", "Under 3.5"),
    "over_4_5":  ("over_4_5", "over",  "Over 4.5"),
    "under_4_5": ("over_4_5", "under", "Under 4.5"),
}

def analyze_value(model_probs: dict, casino_odds: dict | None) -> list[dict]:
    results = []
    for mkey, (og, os, label) in MARKET_MAP.items():
        model_p   = model_probs.get(mkey, 0.0)
        implied_p = None
        edge      = None
        if casino_odds and og in casino_odds:
            g = casino_odds[og]
            if isinstance(g, dict) and os in g:
                implied_p = g[os]
                edge = (model_p - implied_p) / implied_p if implied_p > 0 else None
        results.append({
            "label":       label,
            "market_key":  mkey,
            "model_prob":  round(float(model_p), 4),
            "implied_prob": round(float(implied_p), 4) if implied_p else None,
            "edge":        round(float(edge), 4) if edge is not None else None,
            "has_value":   edge is not None and edge >= VALUE_THRESHOLD,
            "overvalued":  edge is not None and edge <= -VALUE_THRESHOLD,
        })
    return results


def generate_summary(home_team, away_team, lh, la, probs, analysis) -> dict:
    value_bets  = [a for a in analysis if a["has_value"]]
    most_likely = probs["top_scores"][0] if probs["top_scores"] else {}
    return {
        "home_team":         home_team,
        "away_team":         away_team,
        "lambda_home":       lh,
        "lambda_away":       la,
        "win_home":          round(probs["home"] * 100, 1),
        "win_draw":          round(probs["draw"] * 100, 1),
        "win_away":          round(probs["away"] * 100, 1),
        "most_likely_score": most_likely.get("score", "N/A"),
        "most_likely_prob":  round(most_likely.get("prob", 0) * 100, 1),
        "over_2_5":          round(probs["over_2_5"] * 100, 1),
        "btts":              round(probs["btts_yes"] * 100, 1),
        "value_bets":        value_bets,
        "value_count":       len(value_bets),
    }
