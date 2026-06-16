#!/usr/bin/env python3
"""
Actualiza data.js con resultados reales del Mundial 2026.
Usado por GitHub Actions — no requiere DB local ni API key.
Llama directamente a la ESPN API pública.
"""
import json, re, sys, urllib.request
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
FINISHED = {"Final","Full Time","FT","Completed","End Of Period"}
LIVE     = {"In Progress","Halftime","Half Time","2nd Half","Extra Time",
            "Overtime","Penalty","Shootout","1st Half","Break"}

def _fetch_events():
    today = date.today().strftime("%Y%m%d")
    url   = ESPN_URL.format(dates=f"{WC_START}-{today}")
    req   = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("events", [])

def fetch_results():
    results = {}
    for ev in _fetch_events():
        status = (ev.get("status") or {}).get("type", {}).get("description","")
        if status not in FINISHED:
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
        results[frozenset([h_es, a_es])] = {h_es: int(h_sc), a_es: int(a_sc)}
    return results

def fetch_goals():
    """Ranking de goleadores reales desde ESPN. Excluye autogoles."""
    from collections import defaultdict
    gols = defaultdict(lambda: {"goles": 0, "equipo": ""})
    for ev in _fetch_events():
        status = (ev.get("status") or {}).get("type", {}).get("description","")
        if status not in FINISHED:
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
    return sorted(
        [{"nombre": n, "equipo": v["equipo"], "goles": v["goles"]} for n, v in gols.items()],
        key=lambda x: -x["goles"]
    )

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

    # ── Scores: captura partidos TERMINADOS y EN VIVO ──────────────────────────
    results = {}   # frozenset(eq1,eq2) → {eq1: g1, eq2: g2, jugado: bool, en_vivo: bool}
    n_fin = n_live = 0
    for ev in events_cache:
        status = (ev.get("status") or {}).get("type", {}).get("description","")
        is_fin  = status in FINISHED
        is_live = status in LIVE
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
        # Si ya está terminado, no lo sobreescribimos con en_vivo
        key = frozenset([h_es, a_es])
        if key in results and results[key]["jugado"]:
            continue
        results[key] = {h_es: int(h_sc), a_es: int(a_sc),
                        "jugado": is_fin, "en_vivo": is_live}
        if is_fin:  n_fin  += 1
        else:       n_live += 1
    print(f"  {n_fin} finalizados · {n_live} en vivo")

    # ── Fechas ISO para todos los eventos ─────────────────────────────────────
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
    for mg in data["matches_grupos"]:
        key = frozenset([mg["eq1"], mg["eq2"]])

        # Fecha/hora Colombia
        if key in dates_map:
            fecha_col, hora_col = _col_date_time(dates_map[key])
            if mg.get("fecha") != fecha_col or mg.get("hora") != hora_col:
                mg["fecha"], mg["hora"] = fecha_col, hora_col
                changed = True

        if key not in results:
            # Si antes estaba en_vivo pero ya no aparece → limpiar flag
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

    if not changed:
        print("Sin cambios nuevos — data.js ya está al día.")
        sys.exit(0)

    # ── Recalcular puntos (solo partidos TERMINADOS) ───────────────────────────
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
        p["pts"]["total"]  = pts + p["pts"].get("eliminatorias",0) + p["pts"].get("goleador",0)

    data["participants"].sort(key=lambda p: (-p["pts"]["total"], p["nombre"]))
    data["meta"]["partidos_jugados_grupos"] = sum(1 for mg in data["matches_grupos"] if mg["jugado"])

    # ── Goleadores reales (partidos terminados + en vivo) ─────────────────────
    from collections import defaultdict
    gols = defaultdict(lambda: {"goles": 0, "equipo": ""})
    for ev in events_cache:
        status = (ev.get("status") or {}).get("type", {}).get("description","")
        if status not in FINISHED and status not in LIVE:
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

    save_data(data)
    top     = data["participants"][0] if data["participants"] else None
    jugados = data["meta"]["partidos_jugados_grupos"]
    en_vivo_cnt = sum(1 for mg in data["matches_grupos"] if mg.get("en_vivo"))
    print(f"✓ data.js actualizado — {jugados}/72 grupos jugados"
          + (f" · {en_vivo_cnt} en vivo" if en_vivo_cnt else "")
          + f" · {len(goleadores_reales)} goleadores")
    if top:
        print(f"  Líder: {top['nombre']} con {top['pts']['total']} pts")

if __name__ == "__main__":
    main()
