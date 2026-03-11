# ============================================================
#  xg.py — Expected Goals desde understat.com API interna
#  Sin API key. Cubre: EPL, La Liga, Bundesliga, Serie A, Ligue 1
# ============================================================

import json, time, hashlib, re
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

CACHE_DIR = Path("cache/xg")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_TTL = 3600 * 6

LEAGUE_TO_UNDERSTAT = {
    "PL":  "EPL",
    "PD":  "La_liga",
    "BL1": "Bundesliga",
    "SA":  "Serie_A",
    "FL1": "Ligue_1",
}

def _cache_path(key):
    return CACHE_DIR / f"{hashlib.md5(key.encode()).hexdigest()}.json"

def _read_cache(key):
    p = _cache_path(key)
    if p.exists() and (time.time() - p.stat().st_mtime) < CACHE_TTL:
        try: return json.loads(p.read_text())
        except: pass
    return None

def _write_cache(key, data):
    _cache_path(key).write_text(json.dumps(data, ensure_ascii=False))

def _fetch(url):
    req  = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/javascript, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://understat.com/",
    })
    return urlopen(req, timeout=12).read().decode("utf-8")

def _normalize(name):
    return name.lower().replace("fc","").replace("  "," ").strip()

def _name_match(a, b):
    a, b = _normalize(a), _normalize(b)
    return a == b or a in b or b in a

# ── Buscar team_id en understat ───────────────────────────────

def _get_team_id(team_name: str, league: str, season: int = 2024) -> int | None:
    """Obtiene el ID interno de understat para un equipo."""
    cache_key = f"team_id_{league}_{season}_{team_name}"
    cached = _read_cache(cache_key)
    if cached:
        return cached.get("id")

    url = f"https://understat.com/league/{league}/{season}"
    try:
        html = _fetch(url)
    except:
        return None

    # Understat ahora usa teamsData en el HTML
    match = re.search(r"var teamsData\s*=\s*JSON\.parse\('(.+?)'\)", html)
    if not match:
        # Intentar formato alternativo
        match = re.search(r"teamsData\s*=\s*'(.+?)'(?:\s*;|\s*\))", html)
    if not match:
        return None

    try:
        raw  = match.group(1).encode().decode("unicode_escape")
        data = json.loads(raw)
    except:
        return None

    for team_id, team_data in data.items():
        title = team_data.get("title", "")
        if _name_match(team_name, title):
            _write_cache(cache_key, {"id": int(team_id), "title": title})
            return int(team_id)

    return None

# ── Obtener xG del equipo via API interna ─────────────────────

def _fetch_team_xg(team_id: int, n_matches: int = 10) -> dict | None:
    """
    Llama a la API interna de understat para obtener partidos con xG.
    Endpoint: https://understat.com/team/{id}/{season}
    """
    cache_key = f"team_xg_{team_id}"
    cached = _read_cache(cache_key)
    if cached:
        return cached

    url = f"https://understat.com/team/{team_id}/2024"
    try:
        html = _fetch(url)
    except:
        return None

    # Buscar datesData con xG
    match = re.search(r"var datesData\s*=\s*JSON\.parse\('(.+?)'\)", html)
    if not match:
        match = re.search(r"datesData\s*=\s*'(.+?)'(?:\s*;|\s*\))", html)
    if not match:
        return None

    try:
        raw     = match.group(1).encode().decode("unicode_escape")
        matches = json.loads(raw)
    except:
        return None

    finished = [m for m in matches if m.get("isResult") == True]
    if not finished:
        return None

    finished.sort(key=lambda x: x.get("datetime",""), reverse=True)
    recent = finished[:n_matches]

    xg_for     = [float(m.get("xG","0") or 0) for m in recent]
    xg_against = [float(m.get("xGA","0") or 0) for m in recent]

    result = {
        "xg_for":     round(sum(xg_for)     / len(recent), 3),
        "xg_against": round(sum(xg_against) / len(recent), 3),
        "matches":    len(recent),
    }
    _write_cache(cache_key, result)
    return result

# ── Función pública ───────────────────────────────────────────

def get_team_xg(team_name: str, fd_code: str, n_matches: int = 10) -> dict | None:
    """
    Retorna xG promedio del equipo en los últimos n_matches.
    Para CL/EL busca en todas las ligas disponibles.
    """
    leagues_to_try = []
    if fd_code in LEAGUE_TO_UNDERSTAT:
        leagues_to_try.append(LEAGUE_TO_UNDERSTAT[fd_code])
    if fd_code in ("CL", "EL", "WC"):
        leagues_to_try = list(LEAGUE_TO_UNDERSTAT.values())

    for league in leagues_to_try:
        team_id = _get_team_id(team_name, league)
        if not team_id:
            continue
        result = _fetch_team_xg(team_id, n_matches)
        if result:
            result["league"] = league
            return result

    return None
