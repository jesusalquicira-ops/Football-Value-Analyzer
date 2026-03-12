# ============================================================
#  app.py — Football Value Analyzer v3
#  Poisson + Dixon-Coles + xG + Forma Ponderada + H2H + Alertas
# ============================================================
 
import os
from pathlib import Path
 
# Cargar secrets: Streamlit Cloud primero, luego .env local
try:
    import streamlit as _st
    for _k, _v in _st.secrets.items():
        os.environ.setdefault(str(_k), str(_v))
except Exception:
    pass
 
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text(encoding="utf-8-sig").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())
 
import streamlit as st
import pandas as pd
from datetime import datetime, timezone
 
from src.config import LEAGUES, VALUE_THRESHOLD, HOME_ADVANTAGE
from src.odds_api import get_upcoming_fixtures_odds, get_odds_for_event, implied_to_american
from src.football_data import get_team_stats_from_standings, get_league_avg_goals, get_upcoming_fixtures_fd, get_h2h
from src.xg import get_team_xg
from src.model import compute_lambdas, build_score_matrix, compute_market_probs, analyze_value, generate_summary
from src.alerts import load_alerts, save_alerts, merge_alerts, run_full_scan, should_rescan, get_unseen_count, mark_seen
 
# ── Funciones cacheadas en memoria de Streamlit ───────────────
# TTL = 20 min para cuotas, 60 min para fixtures, 12h para stats
# Esto evita llamadas repetidas a la API cuando el usuario recarga
 
@st.cache_data(ttl=3600)
def cached_get_fixtures(odds_key: str):
    return get_upcoming_fixtures_odds(odds_key)
 
@st.cache_data(ttl=1200)  # 20 min
def cached_get_odds(odds_key: str, home: str, away: str, event_id: str):
    return get_odds_for_event(odds_key, home, away, event_id=event_id or None)
 
@st.cache_data(ttl=43200)  # 12 horas
def cached_get_stats(fd_code: str, team_name: str):
    return get_team_stats_from_standings(fd_code, team_name)
 
@st.cache_data(ttl=43200)  # 12 horas
def cached_get_league_avg(fd_code: str):
    return get_league_avg_goals(fd_code)
 
@st.cache_data(ttl=43200)  # 12 horas
def cached_get_h2h(home: str, away: str, fd_code: str):
    return get_h2h(home, away, fd_code)
 
st.set_page_config(page_title="Football Value Analyzer", page_icon="⚽", layout="wide")
 
st.markdown("""<style>
[data-testid="stMetricValue"] { font-size: 1.5rem !important; font-weight: 700; }
.form-W { color: #00e5a0; font-weight: bold; }
.form-D { color: #ffd166; font-weight: bold; }
.form-L { color: #ff4560; font-weight: bold; }
</style>""", unsafe_allow_html=True)
 
def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%d %b  %H:%M")
    except:
        return iso[:16]
 
def _check_keys():
    missing = []
    for k in ["ODDS_API_KEY", "FOOTBALL_DATA_KEY"]:
        v = os.environ.get(k, "").strip()
        if not v or v == "TU_API_KEY_AQUI":
            missing.append(k)
    return missing
 
def _form_html(form: str) -> str:
    icons = {"W": "🟢", "D": "🟡", "L": "🔴"}
    return " ".join(icons.get(c, "⚪") for c in form)
 
# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("⚽ Football Value Analyzer")
    st.caption("Poisson · Dixon-Coles · xG · Forma Ponderada · H2H")
    st.divider()
 
    missing = _check_keys()
    if missing:
        st.error(f"⚠️ Faltan keys: `{'`, `'.join(missing)}`")
        with st.expander("🔍 Diagnóstico"):
            st.code(f"Ruta .env: {_env_path}\nExiste: {_env_path.exists()}\n" +
                    "\n".join(f"{k}: {'✅' if k not in missing else '❌'}" for k in ["ODDS_API_KEY","FOOTBALL_DATA_KEY"]))
        with st.expander("📋 Dónde obtener las keys"):
            st.markdown("**The Odds API:** [the-odds-api.com](https://the-odds-api.com)\n\n"
                        "**football-data.org:** [football-data.org/client/register](https://www.football-data.org/client/register)")
        st.stop()
 
    # Alertas badge
    alerts_data = load_alerts()
    unseen      = get_unseen_count(alerts_data)
    alert_label = f"🔔 Alertas{f'  **({unseen} nuevas)**' if unseen else ''}"
 
    league_name = st.selectbox("🏟️ Competición", list(LEAGUES.keys()),
                               format_func=lambda x: f"{LEAGUES[x]['flag']} {x}")
    cfg = LEAGUES[league_name]
 
    with st.spinner("Cargando partidos..."):
        try:
            fixtures = cached_get_fixtures(cfg["odds_key"])
        except Exception as e:
            st.error(f"Error: {e}")
            fixtures = []
        if not fixtures and cfg["fd_code"]:
            try: fixtures = get_upcoming_fixtures_fd(cfg["fd_code"])
            except: pass
 
    if not fixtures:
        st.warning("No hay partidos próximos.")
        st.stop()
 
    fixture_labels = {f"{f['home_team']} vs {f['away_team']}  —  {_fmt_date(f['date'])}": f for f in fixtures}
    selected = st.selectbox("📅 Partido", list(fixture_labels.keys()))
    fixture  = fixture_labels[selected]
    st.divider()
 
    with st.expander("⚙️ Ajustes del modelo"):
        home_adv   = st.slider("Ventaja local", 0.90, 1.30, HOME_ADVANTAGE, 0.01)
        use_xg     = st.toggle("Usar xG (si disponible)", value=True)
        show_h2h   = st.toggle("Mostrar H2H", value=True)
        min_edge   = st.slider("Edge mínimo alertas", 0.05, 0.20, 0.07, 0.01,
                               format="%.0f%%", help="Ventaja mínima para generar alerta")
 
    run = st.button("🔍 Analizar partido", use_container_width=True, type="primary")
    scan_btn = st.button("🔔 Escanear alertas ahora", use_container_width=True)
 
# ── Main ──────────────────────────────────────────────────────────────────────
page = st.tabs(["📊 Análisis", "🔔 Alertas"])
 
with page[1]:
    st.subheader("🔔 Alertas de Value Bets")
    st.caption("Se escanean automáticamente todos los partidos próximos cada 30 min")
 
    # Escaneo automático o manual
    if scan_btn or should_rescan():
        with st.spinner("Escaneando todas las ligas... (puede tardar 1-2 min)"):
            try:
                new_alerts = run_full_scan(LEAGUES, min_edge=min_edge)
                alerts_data = merge_alerts(load_alerts(), new_alerts)
                save_alerts(alerts_data)
                st.success(f"✅ Escaneo completo. {len(new_alerts)} alertas nuevas encontradas.")
            except Exception as e:
                st.error(f"Error en escaneo: {e}")
 
    alerts_data = load_alerts()
    if not alerts_data:
        st.info("No hay alertas aún. Presiona **Escanear alertas ahora** para buscar value bets en todos los partidos próximos.")
    else:
        unseen_alerts = [a for a in alerts_data if not a.get("seen")]
        seen_alerts   = [a for a in alerts_data if a.get("seen")]
 
        if unseen_alerts:
            st.markdown(f"### 🆕 Nuevas ({len(unseen_alerts)})")
            for a in sorted(unseen_alerts, key=lambda x: x["edge"], reverse=True):
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3,2,1])
                    c1.markdown(f"**{a['home_team']} vs {a['away_team']}**  \n"
                                f"🏆 {a['league']} · 📅 {_fmt_date(a['match_date'])}")
                    c2.markdown(f"**{a['market']}**  \n"
                                f"Modelo: `{a['model_prob']*100:.1f}%` | Casino: `{a['implied_prob']*100:.1f}%`  \n"
                                f"✅ Edge: **`+{a['edge_pct']}%`**")
                    if c3.button("Marcar visto", key=a["id"]):
                        mark_seen(a["id"])
                        st.rerun()
 
        if seen_alerts:
            with st.expander(f"Alertas vistas ({len(seen_alerts)})"):
                rows = [{"Liga": a["league"], "Partido": f"{a['home_team']} vs {a['away_team']}",
                         "Mercado": a["market"], "Edge": f"+{a['edge_pct']}%",
                         "Fecha": _fmt_date(a["match_date"])} for a in seen_alerts]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
 
with page[0]:
    st.markdown(f"## {cfg['flag']} {league_name}")
 
    if not run:
        st.info("👈 Selecciona un partido y presiona **Analizar partido**.")
        rows = [{"Fecha": _fmt_date(f["date"]), "Local": f["home_team"], "Visitante": f["away_team"]}
                for f in fixtures]
        st.subheader(f"Próximos {len(fixtures)} partidos")
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.stop()
 
    c1, _, c2 = st.columns([5,1,5])
    c1.markdown(f"### 🏠 {fixture['home_team']}")
    _.markdown("### vs")
    c2.markdown(f"### ✈️ {fixture['away_team']}")
    st.caption(f"📅 {_fmt_date(fixture['date'])}")
    st.divider()
 
    with st.spinner("Obteniendo datos..."):
        errors = []
        home_stats = away_stats = None
        league_avg = 2.7
        home_xg = away_xg = None
        h2h_data = None
 
        if cfg["fd_code"]:
            try: home_stats = cached_get_stats(cfg["fd_code"], fixture["home_team"])
            except Exception as e: errors.append(f"Stats {fixture['home_team']}: {e}")
            try: away_stats = cached_get_stats(cfg["fd_code"], fixture["away_team"])
            except Exception as e: errors.append(f"Stats {fixture['away_team']}: {e}")
            try: league_avg = cached_get_league_avg(cfg["fd_code"])
            except: pass
            if show_h2h:
                try: h2h_data = cached_get_h2h(fixture["home_team"], fixture["away_team"], cfg["fd_code"])
                except: pass
 
        if use_xg:
            try: home_xg = get_team_xg(fixture["home_team"], cfg.get("fd_code",""))
            except: pass
            try: away_xg = get_team_xg(fixture["away_team"], cfg.get("fd_code",""))
            except: pass
 
        try:
            casino_odds = cached_get_odds(cfg["odds_key"], fixture["home_team"],
                                             fixture["away_team"], fixture.get("event_id", ""))
        except Exception as e:
            errors.append(f"Cuotas: {e}")
            casino_odds = None
 
    for err in errors:
        st.warning(err)
 
    _default_h = {"team_name": fixture["home_team"], "avg_scored": 1.5, "avg_conceded": 1.2,
                  "matches_played": 0, "win_pct": 0, "form": "-----", "position": "—", "points": "—", "clean_sheets": 0}
    _default_a = {**_default_h, "team_name": fixture["away_team"], "avg_scored": 1.2, "avg_conceded": 1.5}
    if not home_stats: home_stats = _default_h
    if not away_stats: away_stats = _default_a
 
    # Usar xG si disponible
    h_scored   = home_xg["xg_for"]     if (use_xg and home_xg) else home_stats["avg_scored"]
    h_conceded = home_xg["xg_against"] if (use_xg and home_xg) else home_stats["avg_conceded"]
    a_scored   = away_xg["xg_for"]     if (use_xg and away_xg) else away_stats["avg_scored"]
    a_conceded = away_xg["xg_against"] if (use_xg and away_xg) else away_stats["avg_conceded"]
 
    xg_note = ""
    if use_xg and (home_xg or away_xg):
        xg_note = "⚡ Usando xG" + (" (local)" if home_xg else "") + (" + xG (visitante)" if away_xg else "")
 
    lh, la  = compute_lambdas(h_scored, h_conceded, a_scored, a_conceded, league_avg, home_adv)
    matrix   = build_score_matrix(lh, la)
    probs    = compute_market_probs(matrix)
    analysis = analyze_value(probs, casino_odds)
    summary  = generate_summary(fixture["home_team"], fixture["away_team"], lh, la, probs, analysis)
 
    # ── Tabs de análisis ──────────────────────────────────────────────────────
    t1, t2, t3, t4 = st.tabs(["📊 Predicción", "🎰 Análisis de Valor", "⚔️ H2H", "📈 Estadísticas"])
 
    with t1:
        if xg_note: st.caption(xg_note)
 
        c1, c2, c3 = st.columns(3)
        c1.metric(f"🏠 {fixture['home_team']}", f"{summary['win_home']}%")
        c2.metric("🤝 Empate", f"{summary['win_draw']}%")
        c3.metric(f"✈️ {fixture['away_team']}", f"{summary['win_away']}%")
        st.divider()
 
        c4, c5, c6 = st.columns(3)
        c4.metric("⚽ λ Local", f"{lh:.2f}", help="Goles esperados local" + (" (xG)" if use_xg and home_xg else ""))
        c5.metric("🎯 Resultado más probable", summary["most_likely_score"], f"{summary['most_likely_prob']}%")
        c6.metric("⚽ λ Visitante", f"{la:.2f}", help="Goles esperados visitante" + (" (xG)" if use_xg and away_xg else ""))
 
        c7, c8 = st.columns(2)
        c7.metric("Over 2.5", f"{summary['over_2_5']}%")
        c8.metric("Ambos anotan", f"{summary['btts']}%")
 
        st.subheader("🎯 Top 5 resultados más probables")
        cols = st.columns(5)
        for i, s in enumerate(probs["top_scores"][:5]):
            cols[i].metric(s["score"], f"{s['prob']*100:.1f}%",
                           delta=implied_to_american(s["prob"]), delta_color="off")
        st.caption(f"📊 Media goles {league_name}: **{league_avg:.2f}**/partido")
 
    with t2:
        # ── Verificar confiabilidad del análisis ──────────────────────────────
        using_defaults = (
            not home_stats or home_stats.get("matches_played", 0) == 0 or
            not away_stats or away_stats.get("matches_played", 0) == 0
        )
        low_lambdas = lh < 1.0 and la < 1.0
        high_edge   = any(abs(a.get("edge", 0)) > 0.25 for a in analysis if a.get("edge") is not None)
 
        if using_defaults:
            st.error(
                "🚫 **Análisis no confiable** — No se encontraron estadísticas reales para uno o ambos equipos. "
                "Los edges mostrados están inflados artificialmente. **No uses este análisis para apostar.**"
            )
        elif low_lambdas and high_edge:
            st.warning(
                "⚠️ **Confiabilidad reducida** — Los goles esperados son muy bajos (λ < 1.0) y los edges parecen inflados. "
                "Verifica las estadísticas antes de usar este análisis."
            )
        else:
            st.success("✅ **Análisis confiable** — Estadísticas reales disponibles para ambos equipos.")
 
        if not casino_odds or not casino_odds.get("bookmaker"):
            st.warning("No se encontraron cuotas para este partido.")
        else:
            st.caption(f"📡 Fuente: **{casino_odds['bookmaker']}**")
            rows = []
            for a in analysis:
                if a["implied_prob"] is None: continue
                rows.append({
                    "Mercado":   a["label"],
                    "Modelo":    f"{a['model_prob']*100:.1f}%",
                    "Casino":    f"{a['implied_prob']*100:.1f}%",
                    "Ventaja":   f"{a['edge']*100:+.1f}%" if a["edge"] else "—",
                    "Veredicto": "✅ VALOR" if a["has_value"] else ("❌ SOBREVALORADO" if a["overvalued"] else "➖ JUSTO"),
                })
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
 
            vb = [a for a in analysis if a["has_value"]]
            if vb:
                st.success(f"⚡ {len(vb)} apuesta(s) con valor (edge ≥ {VALUE_THRESHOLD*100:.0f}%)")
                for a in sorted(vb, key=lambda x: x["edge"], reverse=True):
                    st.markdown(f"**{a['label']}** · Modelo `{a['model_prob']*100:.1f}%` "
                                f"vs Casino `{a['implied_prob']*100:.1f}%` · Edge **`{a['edge']*100:+.1f}%`**")
            else:
                st.info("No se detectaron apuestas con valor claro.")
        st.warning("⚠️ Herramienta educativa. Juega responsablemente.")
 
    with t3:
        if not show_h2h:
            st.info("Activa 'Mostrar H2H' en los ajustes del modelo.")
        elif not h2h_data:
            st.info("No se encontraron enfrentamientos directos previos.")
        else:
            h = h2h_data
            st.subheader(f"⚔️ Últimos {h['matches']} enfrentamientos directos")
 
            c1, c2, c3, c4 = st.columns(4)
            c1.metric(f"🏠 Victorias {fixture['home_team']}", h["home_wins"])
            c2.metric("🤝 Empates", h["draws"])
            c3.metric(f"✈️ Victorias {fixture['away_team']}", h["away_wins"])
            c4.metric("⚽ Goles/partido", h["avg_total_goals"])
 
            st.metric("Ambos anotan (H2H)", f"{h['btts_pct']*100:.0f}%")
 
            st.subheader("Últimos 5 resultados")
            for m in h.get("recent", []):
                gh, ga = m["home_goals"], m["away_goals"]
                if gh > ga:   result_icon = "🟢"
                elif gh == ga: result_icon = "🟡"
                else:          result_icon = "🔴"
                st.markdown(
                    f"{result_icon} **{m['home']} {gh} – {ga} {m['away']}**  "
                    f"<small style='color:gray'>{m['date'][:10]}</small>",
                    unsafe_allow_html=True
                )
 
            # Ajuste del modelo con H2H
            if h["matches"] >= 5:
                st.divider()
                h2h_over = h["avg_total_goals"] > league_avg
                st.info(
                    f"💡 **Insight H2H:** El promedio histórico entre estos equipos es "
                    f"**{h['avg_total_goals']} goles/partido** vs {league_avg:.2f} de la liga. "
                    f"{'Este H2H tiende a ser más goleador de lo normal.' if h2h_over else 'Este H2H tiende a ser más cerrado de lo normal.'}"
                )
 
    with t4:
        ch, ca = st.columns(2)
        for col, stats, xg_stats, label in [
            (ch, home_stats, home_xg, f"🏠 {fixture['home_team']}"),
            (ca, away_stats, away_xg, f"✈️ {fixture['away_team']}"),
        ]:
            with col:
                st.subheader(label)
                if stats.get("matches_played", 0) > 0:
                    st.metric("Posición", stats.get("position","—"))
                    st.metric("Partidos", stats["matches_played"])
 
                    # Goles reales vs xG
                    g1, g2 = st.columns(2)
                    g1.metric("Goles anotados/p (pond.)", stats["avg_scored"])
                    if xg_stats and use_xg:
                        g2.metric("xG anotados/p", xg_stats["xg_for"], help="Expected Goals de understat.com")
                    g3, g4 = st.columns(2)
                    g3.metric("Goles recibidos/p (pond.)", stats["avg_conceded"])
                    if xg_stats and use_xg:
                        g4.metric("xG recibidos/p", xg_stats["xg_against"])
 
                    st.metric("% victorias", f"{stats['win_pct']*100:.0f}%")
                    st.metric("Forma reciente", _form_html(stats.get("form","-----")))
                    st.metric("Puntos", stats.get("points","—"))
                else:
                    st.info("Estadísticas no disponibles.")
 
        st.caption("📊 Forma ponderada: los partidos recientes tienen más peso (decay exponencial)")
        if use_xg:
            st.caption("⚡ xG de understat.com — disponible para Premier, La Liga, Bundesliga, Serie A, Ligue 1")

        if use_xg:
            st.caption("⚡ xG de understat.com — disponible para Premier, La Liga, Bundesliga, Serie A, Ligue 1")
