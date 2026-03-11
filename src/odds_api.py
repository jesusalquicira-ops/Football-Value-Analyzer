# ============================================================
#  odds_api.py — Cliente para the-odds-api.com
#  Doble función: fixture source + cuotas en tiempo real
# ============================================================

import os, json, time, hashlib
from pathlib import Path

import requests
from src.config import CACHE_TTL_ODDS, CACHE_TTL_FIXTURES, PREFERRED_BOOKS

CACHE_DIR = Path("cache/odds")
CACHE_DIR.mkdir(parents=True, exist_ok=True)
BASE_URL  = "https://api.the-odds-api.com/v4"


# ── Cache ────────────────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{hashlib.md5(key.encode()).hexdigest()}.json"

def _read_cache(key: str, ttl: int):
    p = _cache_path(key)
    if p.exists() and (time.time() - p.stat().st_mtime) < ttl:
        return json.loads(p.read_text())
    return None

def _write_cache(key: str, data):
    _cache_path(key).write_text(json.dumps(data, ensure_ascii=False))

def _get(endpoint: str, params: dict, ttl: int = CACHE_TTL_ODDS):
    cache_key = endpoint + json.dumps(params, sort_keys=True)
    cached    = _read_cache(cache_key, ttl)
    if cached is not None:
        return cached

    api_key = os.environ.get("ODDS_API_KEY", "").strip()
    if not api_key or api_key == "TU_API_KEY_AQUI":
        raise ValueError("ODDS_API_KEY no configurada en .env")

    params["apiKey"] = api_key
    resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=10)
    remaining = resp.headers.get("x-requests-remaining", "?")
   # print(f"[OddsAPI] Llamadas restantes: {remaining}")
    resp.raise_for_status()
    data = resp.json()
    _write_cache(cache_key, data)
    return data


# ── Conversiones ─────────────────────────────────────────────────────────────

def decimal_to_implied(d: float) -> float:
    return 1.0 / d if d > 0 else 0.0

def implied_to_american(p: float) -> str:
    if p <= 0 or p >= 1:
        return "N/A"
    if p >= 0.5:
        return f"-{round(p / (1-p) * 100)}"
    return f"+{round((1-p) / p * 100)}"

def remove_vig(probs: dict) -> dict:
    total = sum(probs.values())
    return {k: v / total for k, v in probs.items()} if total else probs


# ── Fixtures desde Odds API ───────────────────────────────────────────────────

def get_upcoming_fixtures_odds(odds_key: str) -> list[dict]:
    """
    Usa The Odds API como fuente de partidos próximos.
    Ventaja: siempre tiene datos actuales independiente del plan.
    """
    try:
        data = _get(
            f"sports/{odds_key}/events",
            {"regions": "eu,uk,us,au"},
            ttl=CACHE_TTL_FIXTURES,
        )
    except Exception as e:
        raise RuntimeError(f"Error obteniendo fixtures de Odds API: {e}")

    if not isinstance(data, list):
        return []

    fixtures = []
    for ev in data:
        fixtures.append({
            "fixture_id": ev.get("id", ""),
            "date":       ev.get("commence_time", ""),
            "home_team":  ev.get("home_team", ""),
            "away_team":  ev.get("away_team", ""),
            "home_id":    ev.get("home_team", "").replace(" ", "_").lower(),
            "away_id":    ev.get("away_team", "").replace(" ", "_").lower(),
            "venue":      "",
            "source":     "the-odds-api",
            "event_id":   ev.get("id", ""),
        })

    # Ordenar por fecha
    fixtures.sort(key=lambda x: x["date"])
    return fixtures


# ── Cuotas ───────────────────────────────────────────────────────────────────

print(f"[DEBUG] Buscando odds: {sport_key} | {home_team} vs {away_team} | event_id: {event_id}")
print(f"[DEBUG] Key configurada: {'SI' if os.environ.get('ODDS_API_KEY') else 'NO'}")

def get_odds_for_event(odds_key: str, home_team: str, away_team: str,
                       event_id: str = None) -> dict | None:
    """
    Obtiene cuotas para un partido específico.
    Si se pasa event_id (de Odds API) es directo; si no, busca por nombre.
    """
    try:
        if event_id:
            data = _get(
                f"sports/{odds_key}/events/{event_id}/odds",
                {
                    "regions":    "eu,uk,us,au",
                    "markets":    "h2h,totals,btts",
                    "oddsFormat": "decimal",
                    "bookmakers": ",".join(PREFERRED_BOOKS),
                },
            )
            # Envolver en lista para reutilizar _parse
            events = [data] if isinstance(data, dict) and "bookmakers" in data else []
        else:
            data = _get(
                f"sports/{odds_key}/odds",
                {
                    "regions":    "eu,uk",
                    "markets":    "h2h,totals,btts",
                    "oddsFormat": "decimal",
                    "bookmakers": ",".join(PREFERRED_BOOKS),
                },
            )
            events = data if isinstance(data, list) else []
    except Exception as e:
        print(f"[OddsAPI] Error cuotas: {e}")
        return None

    if event_id and events:
        return _parse_event_odds(events[0], home_team, away_team)

    event = _find_event(events, home_team, away_team)
    return _parse_event_odds(event, home_team, away_team) if event else None


def _normalize(name: str) -> str:
    return name.lower().replace("fc", "").replace("cf", "").replace("  ", " ").strip()

def _find_event(events: list, home: str, away: str) -> dict | None:
    hn, an = _normalize(home), _normalize(away)
    best, best_score = None, 0
    for ev in events:
        h = _normalize(ev.get("home_team", ""))
        a = _normalize(ev.get("away_team", ""))
        score = (hn in h or h in hn) + (an in a or a in an)
        if score > best_score:
            best, best_score = ev, score
    return best if best_score >= 2 else None


def _parse_event_odds(event: dict, home_team: str, away_team: str) -> dict:
    result = {
        "bookmaker": None,
        "h2h":      {}, "btts":    {},
        "over_0_5": {}, "over_1_5": {}, "over_2_5": {},
        "over_3_5": {}, "over_4_5": {},
        "raw_odds": {},
    }

    bookmakers = event.get("bookmakers", [])
    chosen = None
    for pref in PREFERRED_BOOKS:
        for bm in bookmakers:
            if bm["key"] == pref:
                chosen = bm
                break
        if chosen:
            break
    if not chosen and bookmakers:
        chosen = bookmakers[0]
    if not chosen:
        return result

    result["bookmaker"] = chosen["title"]

    h_name = event.get("home_team", home_team)
    a_name = event.get("away_team", away_team)

    for market in chosen.get("markets", []):
        key      = market["key"]
        outcomes = market["outcomes"]

        if key == "h2h":
            raw = {}
            for o in outcomes:
                if _normalize(o["name"]) == _normalize(h_name):
                    raw["home"] = decimal_to_implied(o["price"])
                elif _normalize(o["name"]) == _normalize(a_name):
                    raw["away"] = decimal_to_implied(o["price"])
                elif o["name"] == "Draw":
                    raw["draw"] = decimal_to_implied(o["price"])
            result["h2h"] = remove_vig(raw)
            result["raw_odds"]["h2h"] = {o["name"]: o["price"] for o in outcomes}

        elif key == "totals":
            totals_raw = {}
            for o in outcomes:
                point = o.get("point", 0)
                side  = "over" if o["name"] == "Over" else "under"
                totals_raw.setdefault(point, {})[side] = decimal_to_implied(o["price"])
            result["raw_odds"]["totals"] = totals_raw
            for line, field in [(0.5, "over_0_5"), (1.5, "over_1_5"), (2.5, "over_2_5"),
                                 (3.5, "over_3_5"), (4.5, "over_4_5")]:
                if line in totals_raw:
                    result[field] = remove_vig(totals_raw[line])

        elif key == "btts":
            raw = {}
            for o in outcomes:
                side = "yes" if o["name"].lower() in ("yes", "sí", "si") else "no"
                raw[side] = decimal_to_implied(o["price"])
            result["btts"] = remove_vig(raw)
            result["raw_odds"]["btts"] = {o["name"]: o["price"] for o in outcomes}

    return result
