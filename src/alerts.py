# ============================================================
#  alerts.py — Sistema de alertas automáticas de value bets
#  Escanea todos los partidos próximos y guarda alertas en JSON
# ============================================================

import json, time
from pathlib import Path
from datetime import datetime, timezone

ALERTS_FILE = Path("alerts/value_alerts.json")
ALERTS_FILE.parent.mkdir(exist_ok=True)
SCAN_TTL    = 60 * 30   # Re-escanear cada 30 minutos


# ── Persistencia ──────────────────────────────────────────────────────────────

def load_alerts() -> list[dict]:
    if ALERTS_FILE.exists():
        try:
            return json.loads(ALERTS_FILE.read_text())
        except:
            pass
    return []

def save_alerts(alerts: list[dict]):
    ALERTS_FILE.write_text(json.dumps(alerts, ensure_ascii=False, indent=2))

def get_last_scan_time() -> float:
    if ALERTS_FILE.exists():
        return ALERTS_FILE.stat().st_mtime
    return 0.0

def should_rescan() -> bool:
    return (time.time() - get_last_scan_time()) > SCAN_TTL


# ── Generación de alertas ─────────────────────────────────────────────────────

def generate_alerts_for_fixture(
    home_team: str, away_team: str, league_name: str,
    match_date: str, analysis: list[dict],
    min_edge: float = 0.07,
) -> list[dict]:
    """
    Genera alertas de value para un partido dado el análisis del modelo.
    min_edge: ventaja mínima para generar alerta (7% por defecto)
    """
    alerts = []
    for a in analysis:
        if a.get("has_value") and a.get("edge", 0) >= min_edge:
            alerts.append({
                "id":          f"{home_team}_{away_team}_{a['market_key']}".replace(" ", "_"),
                "league":      league_name,
                "home_team":   home_team,
                "away_team":   away_team,
                "match_date":  match_date,
                "market":      a["label"],
                "model_prob":  a["model_prob"],
                "implied_prob": a["implied_prob"],
                "edge":        a["edge"],
                "edge_pct":    round(a["edge"] * 100, 1),
                "created_at":  datetime.now(timezone.utc).isoformat(),
                "seen":        False,
            })
    return alerts


def merge_alerts(existing: list[dict], new_alerts: list[dict]) -> list[dict]:
    """Combina alertas nuevas con existentes, evitando duplicados."""
    existing_ids = {a["id"] for a in existing}
    for alert in new_alerts:
        if alert["id"] not in existing_ids:
            existing.append(alert)
            existing_ids.add(alert["id"])

    # Limpiar alertas de partidos que ya pasaron
    now = datetime.now(timezone.utc)
    existing = [
        a for a in existing
        if _parse_date(a.get("match_date", "")) > now
    ]
    return existing


def mark_seen(alert_id: str):
    alerts = load_alerts()
    for a in alerts:
        if a["id"] == alert_id:
            a["seen"] = True
    save_alerts(alerts)


def get_unseen_count(alerts: list[dict]) -> int:
    return sum(1 for a in alerts if not a.get("seen", False))


# ── Escaneo de todas las ligas ────────────────────────────────────────────────

def run_full_scan(leagues: dict, min_edge: float = 0.07) -> list[dict]:
    """
    Escanea todos los partidos próximos de todas las ligas
    y retorna las alertas generadas.
    Solo debe llamarse cuando should_rescan() sea True.
    """
    from src.odds_api import get_upcoming_fixtures_odds, get_odds_for_event
    from src.football_data import get_team_stats_from_standings, get_league_avg_goals
    from src.model import compute_lambdas, build_score_matrix, compute_market_probs, analyze_value
    from src.config import HOME_ADVANTAGE

    all_alerts = []

    for league_name, cfg in leagues.items():
        try:
            fixtures = get_upcoming_fixtures_odds(cfg["odds_key"])
        except:
            continue

        # Limitar a 5 partidos por liga para no agotar llamadas
        for fixture in fixtures[:5]:
            try:
                home, away = fixture["home_team"], fixture["away_team"]
                fd_code    = cfg.get("fd_code")

                # Stats
                home_stats = away_stats = None
                league_avg = 2.7
                if fd_code:
                    try: home_stats = get_team_stats_from_standings(fd_code, home)
                    except: pass
                    try: away_stats = get_team_stats_from_standings(fd_code, away)
                    except: pass
                    try: league_avg = get_league_avg_goals(fd_code)
                    except: pass

                if not home_stats or not away_stats:
                    continue

                # Cuotas
                casino_odds = get_odds_for_event(
                    cfg["odds_key"], home, away,
                    event_id=fixture.get("event_id")
                )
                if not casino_odds:
                    continue

                # Modelo
                lh, la = compute_lambdas(
                    home_stats["avg_scored"], home_stats["avg_conceded"],
                    away_stats["avg_scored"], away_stats["avg_conceded"],
                    league_avg, HOME_ADVANTAGE,
                )
                matrix   = build_score_matrix(lh, la)
                probs    = compute_market_probs(matrix)
                analysis = analyze_value(probs, casino_odds)

                # Generar alertas
                new = generate_alerts_for_fixture(
                    home, away, league_name,
                    fixture["date"], analysis, min_edge
                )
                all_alerts.extend(new)

            except Exception as e:
                print(f"[Alerts] Error en {fixture.get('home_team','?')} vs {fixture.get('away_team','?')}: {e}")
                continue

    return all_alerts


# ── Helper ────────────────────────────────────────────────────────────────────

def _parse_date(iso: str) -> datetime:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except:
        return datetime.min.replace(tzinfo=timezone.utc)
