# ============================================================
#  football_data.py — Cliente football-data.org
#  v2: Forma ponderada + H2H + combinación xG/goles reales
# ============================================================

import os, json, time, hashlib, math
from pathlib import Path
from datetime import datetime, timedelta
import requests

CACHE_DIR = Path("cache/stats")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL  = "https://api.football-data.org/v4"

# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{hashlib.md5(key.encode()).hexdigest()}.json"

def _read_cache(key: str, ttl: int = 21600):
    p = _cache_path(key)
    if p.exists() and (time.time() - p.stat().st_mtime) < ttl:
        return json.loads(p.read_text())
    return None

def _write_cache(key: str, data):
    _cache_path(key).write_text(json.dumps(data, ensure_ascii=False))

def _get(endpoint: str, ttl: int = 21600) -> dict:
    cached = _read_cache(endpoint, ttl)
    if cached:
        return cached
    api_key = os.environ.get("FOOTBALL_DATA_KEY", "").strip()
    if not api_key or api_key == "TU_API_KEY_AQUI":
        raise ValueError("FOOTBALL_DATA_KEY no configurada en .env")
    headers = {"X-Auth-Token": api_key}
    resp = requests.get(f"{BASE_URL}/{endpoint}", headers=headers, timeout=10)
    if resp.status_code == 429:
        raise RuntimeError("Límite de llamadas (10/min). Espera un momento.")
    resp.raise_for_status()
    data = resp.json()
    _write_cache(endpoint, data)
    return data

# ── Mapeo equipo → liga doméstica ─────────────────────────────────────────────

TEAM_TO_DOMESTIC = {
    "Arsenal": "PL", "Chelsea": "PL", "Liverpool": "PL", "Manchester City": "PL",
    "Manchester United": "PL", "Tottenham": "PL", "Tottenham Hotspur": "PL",
    "Aston Villa": "PL", "Newcastle": "PL", "Newcastle United": "PL",
    "Real Madrid": "PD", "Barcelona": "PD", "Atletico Madrid": "PD",
    "Atlético Madrid": "PD", "Athletic Club": "PD", "Villarreal": "PD",
    "Real Sociedad": "PD", "Sevilla": "PD", "Valencia": "PD",
    "Bayern Munich": "BL1", "Borussia Dortmund": "BL1", "Bayer Leverkusen": "BL1",
    "RB Leipzig": "BL1", "Eintracht Frankfurt": "BL1", "Wolfsburg": "BL1",
    "Inter Milan": "SA", "AC Milan": "SA", "Juventus": "SA", "Napoli": "SA",
    "Roma": "SA", "Lazio": "SA", "Atalanta": "SA", "Atalanta BC": "SA",
    "Paris Saint-Germain": "FL1", "Paris Saint Germain": "FL1", "PSG": "FL1",
    "Marseille": "FL1", "Lyon": "FL1", "Monaco": "FL1", "Lille": "FL1", "Brest": "FL1",
    "Ajax": "DED", "PSV Eindhoven": "DED", "Feyenoord": "DED", "AZ": "DED",
    "Sporting CP": "PPL", "Sporting Lisbon": "PPL", "Benfica": "PPL",
    "Porto": "PPL", "Braga": "PPL","Real Sociedad": "PD", "Sevilla": "PD", "Valencia": "PD",
    "Real Betis": "PD", "Girona": "PD", "Osasuna": "PD",
    "Rayo Vallecano": "PD", "Getafe": "PD", "Celta Vigo": "PD",
    "Espanyol": "PD", "Leganes": "PD", "Mallorca": "PD",
}

HAS_STANDINGS = {"PL", "PD", "BL1", "SA", "FL1", "DED", "PPL", "ELC", "BSA"}

def _find_domestic(team_name: str) -> str | None:
    if team_name in TEAM_TO_DOMESTIC:
        return TEAM_TO_DOMESTIC[team_name]
    tl = team_name.lower()
    for k, v in TEAM_TO_DOMESTIC.items():
        if k.lower() in tl or tl in k.lower():
            return v
    return None

def _name_similarity(a: str, b: str) -> float:
    a = a.lower().replace("fc","").replace("cf","").strip()
    b = b.lower().replace("fc","").replace("cf","").strip()
    if a == b: return 1.0
    if a in b or b in a: return 0.8
    wa, wb = set(a.split()), set(b.split())
    if not wa: return 0.0
    return len(wa & wb) / max(len(wa), len(wb))

# ── Forma ponderada (exponential decay) ──────────────────────────────────────

def _weighted_stats_from_matches(matches: list[dict]) -> dict:
    """
    Calcula avg_scored y avg_conceded con decay exponencial.
    El partido más reciente tiene peso e^0=1, el anterior e^(-0.1), etc.
    Esto da aprox 2x más peso al partido más reciente vs el 10mo.
    """
    if not matches:
        return {"avg_scored": 0.0, "avg_conceded": 0.0, "weighted_matches": 0}

    DECAY = 0.1
    total_weight = 0.0
    w_scored = 0.0
    w_conceded = 0.0

    for i, m in enumerate(matches):  # matches ordenados de más reciente a más antiguo
        weight = math.exp(-DECAY * i)
        w_scored   += m["gf"] * weight
        w_conceded += m["ga"] * weight
        total_weight += weight

    return {
        "avg_scored":        round(w_scored   / total_weight, 3),
        "avg_conceded":      round(w_conceded / total_weight, 3),
        "weighted_matches":  len(matches),
    }

# ── Stats desde partidos jugados ──────────────────────────────────────────────

def _stats_from_matches(fd_code: str, team_name: str) -> dict | None:
    today     = datetime.utcnow()
    date_from = (today - timedelta(days=270)).strftime("%Y-%m-%d")
    date_to   = today.strftime("%Y-%m-%d")
    try:
        data = _get(
            f"competitions/{fd_code}/matches?dateFrom={date_from}&dateTo={date_to}&status=FINISHED",
            ttl=21600,
        )
    except:
        return None

    team_matches, found_name = [], None
    for m in data.get("matches", []):
        hn, an = m["homeTeam"]["name"], m["awayTeam"]["name"]
        sh, sa = _name_similarity(team_name, hn), _name_similarity(team_name, an)
        if max(sh, sa) < 0.5:
            continue
        is_home = sh >= sa
        gf = (m["score"]["fullTime"]["home"] or 0) if is_home else (m["score"]["fullTime"]["away"] or 0)
        ga = (m["score"]["fullTime"]["away"] or 0) if is_home else (m["score"]["fullTime"]["home"] or 0)
        team_matches.append({"gf": gf, "ga": ga, "date": m.get("utcDate","")})
        if not found_name:
            found_name = hn if is_home else an

    if not team_matches:
        return None

    # Ordenar de más reciente a más antiguo
    team_matches.sort(key=lambda x: x["date"], reverse=True)

    played   = len(team_matches)
    scored   = sum(m["gf"] for m in team_matches)
    conceded = sum(m["ga"] for m in team_matches)
    won      = sum(1 for m in team_matches if m["gf"] > m["ga"])

    # Forma ponderada
    weighted = _weighted_stats_from_matches(team_matches)

    # Forma últimos 5
    form = "".join(
        "W" if m["gf"]>m["ga"] else ("D" if m["gf"]==m["ga"] else "L")
        for m in team_matches[:5]
    )

    return {
        "team_name":      found_name or team_name,
        "matches_played": played,
        "goals_scored":   scored, "goals_conceded": conceded,
        "avg_scored":     weighted["avg_scored"],     # ← ponderado
        "avg_conceded":   weighted["avg_conceded"],   # ← ponderado
        "avg_scored_raw": round(scored/played, 3),
        "avg_conceded_raw": round(conceded/played, 3),
        "win_pct":        round(won/played, 3),
        "form":           form or "-----",
        "position":       "—", "points": won*3,
        "clean_sheets":   sum(1 for m in team_matches if m["ga"]==0),
    }

# ── Stats desde standings ─────────────────────────────────────────────────────

def _stats_from_standings(fd_code: str, team_name: str) -> dict | None:
    try:
        data = _get(f"competitions/{fd_code}/standings", ttl=21600)
    except:
        return None

    # También traer últimos partidos para forma ponderada
    today     = datetime.utcnow()
    date_from = (today - timedelta(days=120)).strftime("%Y-%m-%d")
    date_to   = today.strftime("%Y-%m-%d")

    recent_matches = []
    try:
        mdata = _get(
            f"competitions/{fd_code}/matches?dateFrom={date_from}&dateTo={date_to}&status=FINISHED",
            ttl=21600,
        )
        for m in mdata.get("matches", []):
            hn, an = m["homeTeam"]["name"], m["awayTeam"]["name"]
            sh, sa = _name_similarity(team_name, hn), _name_similarity(team_name, an)
            if max(sh, sa) < 0.5:
                continue
            is_home = sh >= sa
            gf = (m["score"]["fullTime"]["home"] or 0) if is_home else (m["score"]["fullTime"]["away"] or 0)
            ga = (m["score"]["fullTime"]["away"] or 0) if is_home else (m["score"]["fullTime"]["home"] or 0)
            recent_matches.append({"gf": gf, "ga": ga, "date": m.get("utcDate","")})
        recent_matches.sort(key=lambda x: x["date"], reverse=True)
    except:
        pass

    best_row, best_score = None, 0
    for table in data.get("standings", []):
        for row in table.get("table", []):
            score = _name_similarity(team_name, row["team"]["name"])
            if score > best_score:
                best_score, best_row = score, row

    if not best_row or best_score < 0.4:
        return None

    played   = best_row["playedGames"] or 1
    scored   = best_row["goalsFor"]    or 0
    conceded = best_row["goalsAgainst"] or 0
    won      = best_row["won"]          or 0
    form_raw = best_row.get("form", "") or ""

    # Usar forma ponderada si tenemos partidos recientes, si no usar promedio simple
    if recent_matches:
        weighted = _weighted_stats_from_matches(recent_matches)
        avg_scored   = weighted["avg_scored"]
        avg_conceded = weighted["avg_conceded"]
    else:
        avg_scored   = round(scored/played, 3)
        avg_conceded = round(conceded/played, 3)

    return {
        "team_name":        best_row["team"]["name"],
        "matches_played":   played,
        "goals_scored":     scored, "goals_conceded": conceded,
        "avg_scored":       avg_scored,
        "avg_conceded":     avg_conceded,
        "avg_scored_raw":   round(scored/played, 3),
        "avg_conceded_raw": round(conceded/played, 3),
        "win_pct":          round(won/played, 3),
        "form":             form_raw[-5:] or "-----",
        "position":         best_row.get("position", 0),
        "points":           best_row.get("points", 0),
        "clean_sheets":     0,
    }

# ── Función pública principal ─────────────────────────────────────────────────

def get_team_stats_from_standings(fd_code: str, team_name: str) -> dict | None:
    if fd_code in HAS_STANDINGS:
        return _stats_from_standings(fd_code, team_name)

    # CL/EL: combinar partidos de copa + liga doméstica (sin recursión)
    stats_cup      = _stats_from_matches(fd_code, team_name)
    domestic_code  = _find_domestic(team_name)
    stats_domestic = _stats_from_standings(domestic_code, team_name) if domestic_code else None

    if stats_cup and stats_domestic and stats_cup["matches_played"] >= 2:
        w_c, w_d = 0.35, 0.65
        return {
            "team_name":        stats_cup["team_name"],
            "matches_played":   stats_cup["matches_played"],
            "goals_scored":     stats_cup["goals_scored"],
            "goals_conceded":   stats_cup["goals_conceded"],
            "avg_scored":       round(stats_cup["avg_scored"]*w_c + stats_domestic["avg_scored"]*w_d, 3),
            "avg_conceded":     round(stats_cup["avg_conceded"]*w_c + stats_domestic["avg_conceded"]*w_d, 3),
            "avg_scored_raw":   stats_cup.get("avg_scored_raw", stats_cup["avg_scored"]),
            "avg_conceded_raw": stats_cup.get("avg_conceded_raw", stats_cup["avg_conceded"]),
            "win_pct":          round(stats_cup["win_pct"]*w_c + stats_domestic["win_pct"]*w_d, 3),
            "form":             stats_cup["form"],
            "position":         stats_domestic.get("position", "—"),
            "points":           stats_domestic.get("points", "—"),
            "clean_sheets":     stats_cup.get("clean_sheets", 0),
            "source":           f"Copa 35% + {domestic_code} 65%",
        }

    if stats_domestic: return stats_domestic
    if stats_cup:      return stats_cup
    raise RuntimeError(f"No se encontraron stats para '{team_name}' en {fd_code}")

# ── H2H ───────────────────────────────────────────────────────────────────────

# IDs directos de football-data.org (plan gratuito no permite búsqueda por nombre)
# Fuente: https://www.football-data.org/documentation/quickstart
TEAM_IDS = {
    # Premier League
    "Arsenal": 57, "Arsenal FC": 57,
    "Chelsea": 61, "Chelsea FC": 61,
    "Liverpool": 64, "Liverpool FC": 64,
    "Manchester City": 65, "Manchester City FC": 65,
    "Manchester United": 66, "Manchester United FC": 66,
    "Tottenham": 73, "Tottenham Hotspur": 73, "Tottenham Hotspur FC": 73,
    "Aston Villa": 58, "Newcastle": 67, "Newcastle United": 67,
    "Wolverhampton": 76, "West Ham": 563, "West Ham United": 563,
    "Brighton": 397, "Brentford": 402, "Fulham": 63,
    "Crystal Palace": 354, "Everton": 62, "Leicester City": 338,
    "Nottingham Forest": 351, "Ipswich": 349, "Southampton": 340,
    # La Liga
    "Real Madrid": 86, "Real Madrid CF": 86,
    "Barcelona": 81, "FC Barcelona": 81,
    "Atletico Madrid": 78, "Atlético Madrid": 78, "Club Atlético de Madrid": 78,
    "Athletic Club": 77, "Athletic Bilbao": 77,
    "Real Sociedad": 92, "Villarreal": 94, "Villarreal CF": 94,
    "Sevilla": 559, "Sevilla FC": 559,
    "Valencia": 95, "Valencia CF": 95,
    "Real Betis": 90, "Girona": 298, "Osasuna": 79,
    "Rayo Vallecano": 87, "Getafe": 82, "Celta Vigo": 558,
    "Deportivo Alavés": 263, "Las Palmas": 275, "Mallorca": 89,
    "Espanyol": 80, "Leganes": 745,
    # Bundesliga
    "Bayern Munich": 5, "FC Bayern München": 5,
    "Borussia Dortmund": 4, "BVB": 4,
    "Bayer Leverkusen": 3, "Bayer 04 Leverkusen": 3,
    "RB Leipzig": 721,
    "Eintracht Frankfurt": 19,
    "Wolfsburg": 11, "VfL Wolfsburg": 11,
    "Borussia Mönchengladbach": 18,
    "SC Freiburg": 17, "Union Berlin": 28,
    "VfB Stuttgart": 10, "Werder Bremen": 12,
    "Hoffenheim": 720, "Augsburg": 16,
    "Mainz": 15, "Heidenheim": 44,
    "Holstein Kiel": 721, "St. Pauli": 23,
    # Serie A
    "Inter Milan": 108, "FC Internazionale Milano": 108,
    "AC Milan": 98, "Milan": 98,
    "Juventus": 109, "Juventus FC": 109,
    "Napoli": 113, "SSC Napoli": 113,
    "Roma": 100, "AS Roma": 100,
    "Lazio": 110, "SS Lazio": 110,
    "Atalanta": 102, "Atalanta BC": 102,
    "Fiorentina": 99, "ACF Fiorentina": 99,
    "Bologna": 103, "Torino": 586,
    "Udinese": 115, "Genoa": 107,
    "Lecce": 5890, "Cagliari": 104,
    "Verona": 450, "Venezia": 454,
    "Parma": 112, "Como": 587, "Empoli": 1106, "Monza": 5911,
    # Ligue 1
    "Paris Saint-Germain": 524, "PSG": 524, "Paris Saint Germain": 524,
    "Marseille": 516, "Olympique de Marseille": 516,
    "Lyon": 523, "Olympique Lyonnais": 523,
    "Monaco": 548, "AS Monaco": 548,
    "Lille": 521, "LOSC Lille": 521,
    "Nice": 522, "OGC Nice": 522,
    "Lens": 532, "Rennes": 529,
    "Strasbourg": 576, "Nantes": 543,
    "Reims": 527, "Toulouse": 514,
    "Brest": 3008, "Le Havre": 512,
    "Saint-Etienne": 531, "Montpellier": 518,
    "Angers": 513, "Auxerre": 509,
    # Champions League habituales
    "Ajax": 678, "AFC Ajax": 678,
    "PSV Eindhoven": 674, "PSV": 674,
    "Feyenoord": 675,
    "Benfica": 294, "SL Benfica": 294,
    "Porto": 297, "FC Porto": 297,
    "Sporting CP": 498, "Sporting Lisbon": 498,
    "Braga": 5601,
    "Celtic": 1868,
    "Rangers": 357,
    "Club Brugge": 851,
    "Anderlecht": 246,
    "Shakhtar Donetsk": 714,
    "Dynamo Kyiv": 711,
    "Red Bull Salzburg": 1877,
    "Galatasaray": 2301,
    "Fenerbahce": 2303,
}

def _search_team_id(team_name: str) -> int | None:
    """Busca el ID de un equipo en el diccionario hardcodeado."""
    # Búsqueda exacta
    if team_name in TEAM_IDS:
        return TEAM_IDS[team_name]
    # Búsqueda parcial
    tl = team_name.lower()
    for k, v in TEAM_IDS.items():
        if isinstance(v, int) and (k.lower() in tl or tl in k.lower()):
            return v
    return None

def get_h2h(home_team: str, away_team: str, fd_code: str, n: int = 10) -> dict | None:
    """
    Obtiene H2H usando el endpoint /teams/{id}/matches de football-data.org.
    Busca el ID de cada equipo y luego filtra sus partidos buscando al rival.
    """
    cache_key = f"h2h_v2_{home_team}_{away_team}"
    cached    = _read_cache(cache_key, ttl=3600*12)
    if cached:
        return cached

    # Buscar ID del equipo local
    home_id = _search_team_id(home_team)
    if not home_id:
        return None

    today     = datetime.utcnow()
    date_from = (today - timedelta(days=365*6)).strftime("%Y-%m-%d")
    date_to   = today.strftime("%Y-%m-%d")

    try:
        data = _get(
            f"teams/{home_id}/matches?dateFrom={date_from}&dateTo={date_to}&status=FINISHED",
            ttl=3600*6,
        )
    except:
        return None

    matches = data.get("matches", [])
    if not matches:
        return None

    # Filtrar solo partidos contra el equipo visitante
    h2h_matches = []
    for m in matches:
        hn = m.get("homeTeam", {}).get("name", "")
        an = m.get("awayTeam", {}).get("name", "")
        # Verificar si el rival es el away_team
        if _name_similarity(away_team, hn) > 0.5 or _name_similarity(away_team, an) > 0.5:
            score = m.get("score", {}).get("fullTime", {})
            gh = score.get("home") or 0
            ga = score.get("away") or 0
            # Normalizar para que home_team siempre sea el "local" en nuestra vista
            home_is_our_team = _name_similarity(home_team, hn) > 0.5
            h2h_matches.append({
                "date":       m.get("utcDate", ""),
                "home":       hn,
                "away":       an,
                "home_goals": gh if home_is_our_team else ga,
                "away_goals": ga if home_is_our_team else gh,
                "competition": m.get("competition", {}).get("name", ""),
            })

    if not h2h_matches:
        return None

    # Ordenar más reciente primero, tomar n
    h2h_matches.sort(key=lambda x: x["date"], reverse=True)
    recent = h2h_matches[:n]

    home_wins = sum(1 for m in recent if m["home_goals"] > m["away_goals"])
    draws     = sum(1 for m in recent if m["home_goals"] == m["away_goals"])
    away_wins = sum(1 for m in recent if m["home_goals"] < m["away_goals"])
    avg_total = round(sum(m["home_goals"]+m["away_goals"] for m in recent) / len(recent), 2)
    btts_pct  = round(sum(1 for m in recent if m["home_goals"]>0 and m["away_goals"]>0) / len(recent), 2)

    result = {
        "matches":         len(recent),
        "home_wins":       home_wins,
        "draws":           draws,
        "away_wins":       away_wins,
        "avg_total_goals": avg_total,
        "btts_pct":        btts_pct,
        "recent":          recent[:5],
    }
    _write_cache(cache_key, result)
    return result

# ── Media de goles de la liga ─────────────────────────────────────────────────

def get_league_avg_goals(fd_code: str) -> float:
    today     = datetime.utcnow()
    date_from = (today - timedelta(days=270)).strftime("%Y-%m-%d")
    date_to   = today.strftime("%Y-%m-%d")
    try:
        data    = _get(
            f"competitions/{fd_code}/matches?dateFrom={date_from}&dateTo={date_to}&status=FINISHED",
            ttl=21600,
        )
        matches = data.get("matches", [])
        if matches:
            total = sum(
                (m["score"]["fullTime"]["home"] or 0) + (m["score"]["fullTime"]["away"] or 0)
                for m in matches
            )
            return round(total / len(matches), 3)
    except:
        pass
    try:
        data   = _get(f"competitions/{fd_code}/standings", ttl=21600)
        tables = data.get("standings", [])
        table  = next((t for t in tables if t.get("type")=="TOTAL"), tables[0] if tables else None)
        if table:
            rows  = table.get("table", [])
            goals = sum(r["goalsFor"] for r in rows)
            games = sum(r["playedGames"] for r in rows) // 2
            if games: return round(goals/games, 3)
    except:
        pass
    return 2.7

# ── Fixtures ──────────────────────────────────────────────────────────────────

def get_upcoming_fixtures_fd(fd_code: str, days_ahead: int = 14) -> list[dict]:
    today   = datetime.utcnow()
    date_to = (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    try:
        data = _get(
            f"competitions/{fd_code}/matches?dateFrom={today.strftime('%Y-%m-%d')}&dateTo={date_to}&status=SCHEDULED",
            ttl=1800,
        )
    except:
        return []
    return [
        {
            "fixture_id": str(m["id"]),
            "date":       m["utcDate"],
            "home_team":  m["homeTeam"]["name"],
            "away_team":  m["awayTeam"]["name"],
            "home_id":    str(m["homeTeam"]["id"]),
            "away_id":    str(m["awayTeam"]["id"]),
            "venue":      "",
            "source":     "football-data.org",
        }
        for m in data.get("matches", [])
    ]
