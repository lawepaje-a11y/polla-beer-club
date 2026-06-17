#!/usr/bin/env python3
"""
Actualiza data.js con resultados reales del Mundial 2026.
Usado por GitHub Actions — no requiere DB local ni API key.
Llama directamente a la ESPN API pública.
"""
import json, re, sys, urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

_COL = timezone(timedelta(hours=-5))

def _col_date_time(iso):
    if not iso:
        return None, None
    try:
        s = iso if (iso.endswith("Z") or "+" in iso[10:]) else iso + "Z"
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        col = dt.astimezone(_COL)
        return col.strftime("%Y-%m-%d"), col.strftime("%I:%M %p")
    except Exception:
        return None, None

HERE    = Path(__file__).parent
DATA_JS = HERE / "data.js"
WC_START = "20260611"
ESPN_URL = ("https://site.api.espn.com/apis/site/v2/sports/soccer"
            "/fifa.world/scoreboard?dates={dates}&limit=100")

ESPN_A_ES = {
    "Mexico":"México","South Africa":"Sudáfrica","South Korea":"Corea del Sur",
    "Czechia":"Chequia","Czech Republic":"Chequia","Canada":"Canadá",
    "Bosnia-Herzegovina":"Bosnia y Herz.","Bosnia and Herzegovina":"Bosnia y Herz.",
    "United States":"Estados Unidos","Paraguay":"Paraguay","Qatar":"Catar",
    "Switzerland":"Suiza","Brazil":"Brasil","Morocco":"Marruecos",
    "Haiti":"Haití","Scotland":"Escocia","Australia":"Australia",
    "Türkiye":"Turquía","Turkey":"Turquía","Germany":"Alemania",
    "Curaçao":"Curazao","Curacao":"Curazao","Netherlands":"Países Bajos",
    "Japan":"Japón","Ivory Coast":"Costa de Marfil","Côte d'Ivoire":"Costa de Marfil",
    "Ecuador":"Ecuador","Sweden":"Suecia","Tunisia":"Túnez",
    "Belgium":"Bélgica","Egypt":"Egipto","Iran":"Irán","New Zealand":"Nueva Zelanda",
    "Spain":"España","Cape Verde":"Cabo Verde","Saudi Arabia":"Arabia Saudita",
    "Uruguay":"Uruguay","France":"Francia","Senegal":"Senegal","Iraq":"Irak",
    "Norway":"Noruega","Argentina":"Argentina","Algeria":"Argelia","Austria":"Austria",
    "Jordan":"Jordania","Portugal":"Portugal","Congo":"Congo","DR Congo":"Congo",
    "Congo DR":"Congo","Uzbekistan":"Uzbekistán","Colombia":"Colombia",
    "England":"Inglaterra","Croatia":"Croacia","Ghana":"Ghana","Panama":"Panamá",
}

# ESPN usa state='post' (terminado) y state='in' (en curso) — más robusto que description
FINISHED = {"Final","Full Time","FT","Completed","End Of Period"}  # fallback
LIVE     = {"In Progress","Halftime","Half Time","2nd Half","Extra Time",
            "Overtime","Penalty","Shootout","1st Half","First Half",
            "Second Half","2nd Half","Break"}  # fallback

def _espn_status(ev):
    """Retorna (is_fin, is_live) usando state de ESPN — fiable ante cambios de description."""
    st = (ev.get("status") or {}).get("type", {})
    state = st.get("state", "")
    if state == "post":
        return True, False
    if state == "in":
        return False, True
    # fallback por description
    desc = st.get("description", "")
    return desc in FINISHED, desc in LIVE

# Mapeo rondas ESPN → fases internas
ESPN_ROUND_TO_FASE = {
    "round of 32":    "DIECISEISAVOS",
    "round of 16":    "OCTAVOS",
    "quarterfinals":  "CUARTOS",
    "quarterfinal":   "CUARTOS",
    "semifinals":     "SEMIFINAL",
    "semifinal":      "SEMIFINAL",
    "third place":    "TERCER_PUESTO",
    "third-place":    "TERCER_PUESTO",
    "final":          "FINAL",
}


def _fetch_events():
    today = date.today().strftime("%Y%m%d")
    url   = ESPN_URL.format(dates=f"{WC_START}-{today}")
    req   = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("events", [])


def _espn_fase(ev):
    """Detecta la fase de un evento ESPN desde notes del evento o del competition."""
    # Intentar desde notes del competition
    comp = (ev.get("competitions") or [{}])[0]
    for note in (comp.get("notes") or []):
        text = (note.get("headline") or note.get("text") or "").lower()
        for key, fase in ESPN_ROUND_TO_FASE.items():
            if key in text:
                return fase
    # Intentar desde el nombre del evento
    name = (ev.get("name") or ev.get("shortName") or "").lower()
    for key, fase in ESPN_ROUND_TO_FASE.items():
        if key in name:
            return fase
    # Intentar desde season type notes
    season = ev.get("season") or {}
    for note in (season.get("notes") or []):
        text = (note.get("headline") or note.get("text") or "").lower()
        for key, fase in ESPN_ROUND_TO_FASE.items():
            if key in text:
                return fase
    return None


# ── Eliminatorias ─────────────────────────────────────────────────────────────

def update_elim_results(data, events_cache):
    """Asigna equipos y resultados a matches_elim desde ESPN. Retorna True si hubo cambios."""
    me = {m["numero"]: m for m in data["matches_elim"]}

    # Partidos sin equipos asignados, por fase (para asignación secuencial)
    by_fase = defaultdict(list)
    for m in data["matches_elim"]:
        if not m.get("jugado") and not m.get("eq1_real"):
            by_fase[m["fase"]].append(m["numero"])

    # Partidos ya con equipos → indexar para update rápido
    assigned = {}
    for m in data["matches_elim"]:
        if m.get("eq1_real") and m.get("eq2_real"):
            assigned[frozenset([m["eq1_real"], m["eq2_real"]])] = m["numero"]

    changed = False

    for ev in events_cache:
        is_fin, is_live = _espn_status(ev)
        if not is_fin and not is_live:
            continue

        fase = _espn_fase(ev)
        if not fase:
            continue

        comp  = (ev.get("competitions") or [{}])[0]
        comps = comp.get("competitors", [])
        if len(comps) < 2:
            continue

        home = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
        away = next((c for c in comps if c.get("homeAway") == "away"), comps[1])
        h_es = ESPN_A_ES.get((home.get("team") or {}).get("displayName", ""))
        a_es = ESPN_A_ES.get((away.get("team") or {}).get("displayName", ""))
        if not h_es or not a_es:
            continue

        key = frozenset([h_es, a_es])

        if key in assigned:
            num = assigned[key]
        elif by_fase[fase]:
            num = by_fase[fase].pop(0)
            assigned[key] = num
        else:
            print(f"  [elim] sin slot para {fase}: {h_es} vs {a_es}")
            continue

        m = me[num]
        # No sobreescribir un partido ya finalizado con datos en vivo
        if m.get("jugado") and is_live:
            continue

        # Detectar ganador (para FINAL y TERCER_PUESTO)
        eq_ganador = None
        if is_fin:
            h_sc = home.get("score", "")
            a_sc = away.get("score", "")
            if str(h_sc).isdigit() and str(a_sc).isdigit():
                if   int(h_sc) > int(a_sc): eq_ganador = h_es
                elif int(a_sc) > int(h_sc): eq_ganador = a_es
                else:
                    # empate → penales: ESPN marca winner=True en el clasificado
                    for c in comps:
                        if c.get("winner"):
                            t = (c.get("team") or {}).get("displayName", "")
                            eq_ganador = ESPN_A_ES.get(t, t)
                            break

        upd = {
            "eq1_real":   h_es,
            "eq2_real":   a_es,
            "eq_ganador": eq_ganador,
            "jugado":     is_fin,
            "en_vivo":    is_live,
        }
        for k, v in upd.items():
            if m.get(k) != v:
                m[k] = v
                changed = True

    # Limpiar en_vivo de partidos que ya no aparecen como LIVE en ESPN
    live_keys = set()
    for ev in events_cache:
        _, is_live_chk = _espn_status(ev)
        if not is_live_chk:
            continue
        comp  = (ev.get("competitions") or [{}])[0]
        comps = comp.get("competitors", [])
        if len(comps) < 2:
            continue
        home = next((c for c in comps if c.get("homeAway") == "home"), comps[0])
        away = next((c for c in comps if c.get("homeAway") == "away"), comps[1])
        h_es = ESPN_A_ES.get((home.get("team") or {}).get("displayName", ""))
        a_es = ESPN_A_ES.get((away.get("team") or {}).get("displayName", ""))
        if h_es and a_es:
            live_keys.add(frozenset([h_es, a_es]))

    for m in data["matches_elim"]:
        if m.get("en_vivo"):
            key = frozenset([m.get("eq1_real"), m.get("eq2_real")])
            if key not in live_keys:
                m["en_vivo"] = False
                changed = True

    return changed


def pts_elim(fase, p_eq1, p_eq2, eq1_real, eq2_real, eq_ganador):
    """Espejo exacto de v_puntos_eliminatorias en Python."""
    real_set = {x for x in [eq1_real, eq2_real] if x}
    FASE_BASE = {"DIECISEISAVOS": 3, "OCTAVOS": 5, "CUARTOS": 10, "SEMIFINAL": 20}

    if fase in FASE_BASE:
        base = FASE_BASE[fase]
        pts  = base if p_eq1 in real_set else 0
        pts += base if (p_eq2 and p_eq2 != p_eq1 and p_eq2 in real_set) else 0
        return pts
    elif fase == "FINAL":
        def _f(eq):
            if not eq: return 0
            if eq == eq_ganador:  return 50
            if eq in real_set:    return 30
            return 0
        return _f(p_eq1) + (_f(p_eq2) if p_eq2 != p_eq1 else 0)
    elif fase == "TERCER_PUESTO":
        def _t(eq):
            if not eq: return 0
            if eq == eq_ganador:  return 20
            if eq in real_set:    return 10
            return 0
        return _t(p_eq1) + (_t(p_eq2) if p_eq2 != p_eq1 else 0)
    return 0


def recalc_elim_pts(data):
    """Recalcula pts["eliminatorias"] para todos los participantes."""
    me     = {m["numero"]: m for m in data["matches_elim"]}
    preds_e = (data.get("predictions") or {}).get("eliminatorias", {})
    for p in data["participants"]:
        preds = preds_e.get(str(p["id"]), {})
        total = 0
        for n_str, eq_pair in preds.items():
            m = me.get(int(n_str))
            if not m or not m.get("jugado"):
                continue
            eq1p = eq_pair[0] if len(eq_pair) > 0 else None
            eq2p = eq_pair[1] if len(eq_pair) > 1 else None
            total += pts_elim(m["fase"], eq1p, eq2p,
                              m.get("eq1_real"), m.get("eq2_real"), m.get("eq_ganador"))
        p["pts"]["eliminatorias"] = total


def recalc_goleador_pts(data, goleadores_reales):
    """Auto-detecta goleador cuando la Final se ha jugado y asigna 20 pts."""
    final = next((m for m in data["matches_elim"] if m["fase"] == "FINAL"), None)

    if not final or not final.get("jugado") or not goleadores_reales:
        # Torneo en curso — no confirmar goleador
        data["meta"]["goleador_real"] = data["meta"].get("goleador_real")  # conservar si ya existía
        for p in data["participants"]:
            p["pts"]["goleador"] = 0
        return

    # Torneo terminado: máximo anotador (primero de la lista ESPN, ya ordenada por -goles)
    goleador = goleadores_reales[0]["nombre"]
    data["meta"]["goleador_real"] = goleador
    print(f"  Goleador auto-confirmado: {goleador} ({goleadores_reales[0]['goles']} goles)")

    for p in data["participants"]:
        pts = 20 if (p.get("goleador") or "").strip().lower() == goleador.strip().lower() else 0
        p["pts"]["goleador"] = pts


def pts_grupo(g1p, g2p, g1r, g2r):
    if None in [g1p, g2p, g1r, g2r]:
        return 0
    g1p,g2p,g1r,g2r = int(g1p),int(g2p),int(g1r),int(g2r)
    if g1p==g1r and g2p==g2r:
        return 3
    return 1 if ((g1p>g2p)==(g1r>g2r) and (g1p<g2p)==(g1r<g2r)) else 0


def load_data():
    raw  = DATA_JS.read_text(encoding="utf-8")
    idx  = raw.index("window.POLLA_DATA")
    json_part = raw[raw.index("=", idx)+1:].strip().rstrip(";\n").strip()
    return json.loads(json_part)


def save_data(data):
    prefix = "// Generado automáticamente por update_data.py — no editar manualmente\nwindow.POLLA_DATA = "
    DATA_JS.write_text(prefix + json.dumps(data, ensure_ascii=False, indent=2) + ";\n", encoding="utf-8")


def main():
    print("Leyendo data.js...")
    data = load_data()

    print("Consultando ESPN API...")
    events_cache = _fetch_events()

    # ── Scores: grupos TERMINADOS y EN VIVO ───────────────────────────────────
    results = {}
    n_fin = n_live = 0
    for ev in events_cache:
        is_fin, is_live = _espn_status(ev)
        if not is_fin and not is_live:
            continue
        comps = (ev.get("competitions") or [{}])[0].get("competitors", [])
        if len(comps) < 2:
            continue
        home = next((c for c in comps if c.get("homeAway")=="home"), comps[0])
        away = next((c for c in comps if c.get("homeAway")=="away"), comps[1])
        h_es = ESPN_A_ES.get((home.get("team") or {}).get("displayName",""))
        a_es = ESPN_A_ES.get((away.get("team") or {}).get("displayName",""))
        if not h_es or not a_es:
            continue
        h_sc, a_sc = home.get("score",""), away.get("score","")
        if not str(h_sc).isdigit() or not str(a_sc).isdigit():
            continue
        key = frozenset([h_es, a_es])
        if key in results and results[key]["jugado"]:
            continue
        results[key] = {h_es: int(h_sc), a_es: int(a_sc),
                        "jugado": is_fin, "en_vivo": is_live}
        if is_fin:  n_fin  += 1
        else:       n_live += 1
    print(f"  Grupos: {n_fin} finalizados · {n_live} en vivo")

    # ── Fechas ISO para partidos de grupos ────────────────────────────────────
    dates_map = {}
    for ev in events_cache:
        raw_date = (ev.get("date") or "").strip()
        if not raw_date:
            continue
        comps = (ev.get("competitions") or [{}])[0].get("competitors", [])
        if len(comps) < 2:
            continue
        home = next((c for c in comps if c.get("homeAway")=="home"), comps[0])
        away = next((c for c in comps if c.get("homeAway")=="away"), comps[1])
        h_es = ESPN_A_ES.get((home.get("team") or {}).get("displayName",""))
        a_es = ESPN_A_ES.get((away.get("team") or {}).get("displayName",""))
        if h_es and a_es:
            dates_map[frozenset([h_es, a_es])] = raw_date

    changed = False

    # ── Actualizar matches_grupos ──────────────────────────────────────────────
    for mg in data["matches_grupos"]:
        key = frozenset([mg["eq1"], mg["eq2"]])

        if key in dates_map:
            fecha_col, hora_col = _col_date_time(dates_map[key])
            if mg.get("fecha") != fecha_col or mg.get("hora") != hora_col:
                mg["fecha"], mg["hora"] = fecha_col, hora_col
                changed = True

        if key not in results:
            if mg.get("en_vivo"):
                mg["en_vivo"] = False
                changed = True
            continue

        r       = results[key]
        g1r     = r[mg["eq1"]]
        g2r     = r[mg["eq2"]]
        jugado  = r["jugado"]
        en_vivo = r["en_vivo"]

        if (mg["g1_real"] != g1r or mg["g2_real"] != g2r
                or mg["jugado"] != jugado or mg.get("en_vivo") != en_vivo):
            mg["g1_real"]  = g1r
            mg["g2_real"]  = g2r
            mg["jugado"]   = jugado
            mg["en_vivo"]  = en_vivo
            changed = True

    # ── Actualizar matches_elim ────────────────────────────────────────────────
    if update_elim_results(data, events_cache):
        changed = True

    if not changed:
        print("Sin cambios nuevos — data.js ya está al día.")
        sys.exit(0)

    # ── Recalcular puntos de grupos ───────────────────────────────────────────
    result_map = {mg["numero"]: mg for mg in data["matches_grupos"]}
    preds_g    = (data.get("predictions") or {}).get("grupos", {})
    for p in data["participants"]:
        preds = preds_g.get(str(p["id"]), {})
        pts = sum(
            pts_grupo(pr[0], pr[1], result_map[int(n)]["g1_real"], result_map[int(n)]["g2_real"])
            for n, pr in preds.items()
            if int(n) in result_map and result_map[int(n)]["jugado"]
        )
        p["pts"]["grupos"] = pts

    # ── Recalcular puntos de eliminatorias ────────────────────────────────────
    recalc_elim_pts(data)

    # ── Goleadores reales (terminados + en vivo) ──────────────────────────────
    gols = defaultdict(lambda: {"goles": 0, "equipo": ""})
    for ev in events_cache:
        is_fin_g, is_live_g = _espn_status(ev)
        if not is_fin_g and not is_live_g:
            continue
        comp = (ev.get("competitions") or [{}])[0]
        team_map = {}
        for c in comp.get("competitors", []):
            t = c.get("team") or {}
            team_map[t.get("id","")] = ESPN_A_ES.get(t.get("displayName",""), t.get("displayName",""))
        for d in comp.get("details", []):
            if not d.get("scoringPlay") or d.get("ownGoal"):
                continue
            team_id = (d.get("team") or {}).get("id","")
            equipo  = team_map.get(team_id, "")
            for ath in d.get("athletesInvolved", []):
                nombre = (ath.get("fullName") or "").replace(" null","").strip()
                if nombre:
                    gols[nombre]["goles"] += 1
                    if not gols[nombre]["equipo"]:
                        gols[nombre]["equipo"] = equipo

    votos_map = {p["goleador"]: 0 for p in data["participants"] if p.get("goleador")}
    for p in data["participants"]:
        g = p.get("goleador")
        if g:
            votos_map[g] = votos_map.get(g, 0) + 1

    goleadores_reales = sorted(
        [{"nombre": n, "equipo": v["equipo"], "goles": v["goles"],
          "votos_polla": votos_map.get(n, 0)} for n, v in gols.items()],
        key=lambda x: -x["goles"]
    )
    data["goleadores_reales"] = goleadores_reales

    # ── Puntos de goleador (auto-detectado al finalizar el torneo) ────────────
    recalc_goleador_pts(data, goleadores_reales)

    # ── Totales y orden final ─────────────────────────────────────────────────
    for p in data["participants"]:
        p["pts"]["total"] = (p["pts"].get("grupos", 0)
                           + p["pts"].get("eliminatorias", 0)
                           + p["pts"].get("goleador", 0))

    data["participants"].sort(key=lambda p: (-p["pts"]["total"], p["nombre"]))
    data["meta"]["partidos_jugados_grupos"] = sum(1 for mg in data["matches_grupos"] if mg["jugado"])
    data["meta"]["partidos_jugados_elim"]   = sum(1 for m  in data["matches_elim"]   if m.get("jugado"))

    # ── Guardar ───────────────────────────────────────────────────────────────
    save_data(data)

    jugados_g    = data["meta"]["partidos_jugados_grupos"]
    jugados_e    = data["meta"]["partidos_jugados_elim"]
    en_vivo_g    = sum(1 for mg in data["matches_grupos"] if mg.get("en_vivo"))
    en_vivo_e    = sum(1 for m  in data["matches_elim"]   if m.get("en_vivo"))
    en_vivo_total = en_vivo_g + en_vivo_e
    top = data["participants"][0] if data["participants"] else None

    print(f"✓ data.js actualizado — {jugados_g}/72 grupos · {jugados_e}/32 elim"
          + (f" · {en_vivo_total} en vivo" if en_vivo_total else "")
          + f" · {len(goleadores_reales)} goleadores")
    if top:
        print(f"  Líder: {top['nombre']} con {top['pts']['total']} pts")


if __name__ == "__main__":
    main()
