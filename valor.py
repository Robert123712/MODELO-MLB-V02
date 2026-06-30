# ============================================================
# MODULO DE VALOR — compara el modelo contra la linea real
# Detecta apuestas +EV (Expected Value) y de-viguea el mercado.
# Usa The Odds API (the-odds-api.com). Plan free: ~500 req/mes.
#   set ODDS_API_KEY=tu_clave   (variable de entorno)
# Si no hay clave, el modelo corre igual y solo omite esta seccion.
# ============================================================

import os
import json
import urllib.request
import urllib.parse

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "").strip()
UMBRAL_EV = 0.03   # solo resalta jugadas con EV > 3%

# Apodos de los 30 equipos. Normaliza nombres entre MLB StatsAPI y The Odds API
# (p.ej. "Athletics" vs "Oakland Athletics") para que el cruce no falle en silencio.
_APODOS = [
    "Diamondbacks", "Braves", "Orioles", "Red Sox", "White Sox", "Cubs", "Reds",
    "Guardians", "Rockies", "Tigers", "Astros", "Royals", "Angels", "Dodgers",
    "Marlins", "Brewers", "Twins", "Yankees", "Mets", "Athletics", "Phillies",
    "Pirates", "Padres", "Giants", "Mariners", "Cardinals", "Rays", "Rangers",
    "Blue Jays", "Nationals",
]

def _canon(nombre):
    """Reduce cualquier variante de nombre a su apodo canonico ('Oakland Athletics'
    y 'Athletics' -> 'Athletics'). Si no reconoce el equipo, usa el nombre limpio."""
    n = (nombre or "").lower()
    for apodo in _APODOS:
        if apodo.lower() in n:
            return apodo
    return n.strip()

def buscar(odds_slate, visita, casa):
    """Cruce tolerante a diferencias de nombre entre las dos APIs."""
    return odds_slate.get((_canon(visita), _canon(casa)))

# ---------------- CONVERSIONES ----------------

def americano_a_decimal(m):
    m = float(m)
    return 1 + (m / 100 if m > 0 else 100 / -m)

def decimal_a_prob(d):
    return 1 / d if d else 0.0

def devig_dos_vias(p1, p2):
    """Quita el margen de la casa repartiendo la sobre-redondez (overround)."""
    s = p1 + p2
    if s <= 0:
        return p1, p2
    return p1 / s, p2 / s

def ev_por_unidad(p_model, dec):
    """Ganancia esperada por 1 unidad apostada a momio decimal 'dec'."""
    return p_model * (dec - 1) - (1 - p_model)

# ---------------- DESCARGA DE LINEAS ----------------

def obtener_odds():
    """Devuelve {(visita, casa): registro_odds} o {} si no hay clave/red."""
    if not ODDS_API_KEY:
        return {}
    params = urllib.parse.urlencode({
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h,totals",
        "oddsFormat": "american",
    })
    url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?{params}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.load(r)
    except Exception as e:
        print(f"⚠ No se pudieron bajar las lineas: {e}")
        return {}
    salida = {}
    for ev in data:
        clave = (_canon(ev.get("away_team")), _canon(ev.get("home_team")))
        salida[clave] = ev
    return salida

# ---------------- ANALISIS DE UN JUEGO ----------------

def _mejor_precio(odds_evento, mercado, nombre):
    """Mejor momio (mas alto = mejor para el apostador) entre las casas."""
    mejor = None
    for casa in odds_evento.get("bookmakers", []):
        for m in casa.get("markets", []):
            if m["key"] != mercado:
                continue
            for o in m["outcomes"]:
                if o["name"] != nombre:
                    continue
                if mejor is None or o["price"] > mejor[0]:
                    mejor = (o["price"], o.get("point"), casa["title"])
    return mejor  # (precio_americano, punto, casa) o None

def _linea_total_consenso(odds_evento):
    """Punto (linea) de total mas repetido entre las casas. Evita el bug de comparar
    el Over de una linea contra el Under de otra al de-viguear."""
    from collections import Counter
    puntos = Counter()
    for casa in odds_evento.get("bookmakers", []):
        for m in casa.get("markets", []):
            if m["key"] != "totals":
                continue
            for o in m["outcomes"]:
                if o["name"] == "Over" and o.get("point") is not None:
                    puntos[o["point"]] += 1
    return puntos.most_common(1)[0][0] if puntos else None

def _mejor_precio_linea(odds_evento, mercado, nombre, punto):
    """Mejor momio para 'nombre' SOLO en el punto dado (ambos lados en la misma linea)."""
    mejor = None
    for casa in odds_evento.get("bookmakers", []):
        for m in casa.get("markets", []):
            if m["key"] != mercado:
                continue
            for o in m["outcomes"]:
                if o["name"] != nombre or o.get("point") != punto:
                    continue
                if mejor is None or o["price"] > mejor[0]:
                    mejor = (o["price"], o.get("point"), casa["title"])
    return mejor

def _prob_over_interp(overs, linea):
    """Interpola la prob de over del modelo a la linea exacta del mercado."""
    ks = sorted(overs)
    if linea <= ks[0]:
        return overs[ks[0]]
    if linea >= ks[-1]:
        return overs[ks[-1]]
    for a, b in zip(ks, ks[1:]):
        if a <= linea <= b:
            t = (linea - a) / (b - a)
            return overs[a] + t * (overs[b] - overs[a])
    return overs[ks[len(ks) // 2]]

def analizar_juego(odds_evento, visita, casa, p_casa, overs):
    """Compara modelo vs mercado. Devuelve lista de jugadas con valor."""
    jugadas = []
    if not odds_evento:
        return jugadas

    # ---- Moneyline ----
    ml_casa = _mejor_precio(odds_evento, "h2h", casa)
    ml_visita = _mejor_precio(odds_evento, "h2h", visita)
    if ml_casa and ml_visita:
        dc = americano_a_decimal(ml_casa[0])
        dv = americano_a_decimal(ml_visita[0])
        # prob justa del mercado (de-vigueada) solo informativa
        fair_c, _ = devig_dos_vias(decimal_a_prob(dc), decimal_a_prob(dv))
        for lado, p_mod, dec, precio, libro in [
            (casa, p_casa, dc, ml_casa[0], ml_casa[2]),
            (visita, 1 - p_casa, dv, ml_visita[0], ml_visita[2]),
        ]:
            ev = ev_por_unidad(p_mod, dec)
            if ev > UMBRAL_EV:
                jugadas.append({
                    "mercado": "ML", "pick": lado, "linea": "",
                    "p_modelo": p_mod, "p_mercado": fair_c if lado == casa else 1 - fair_c,
                    "momio": precio, "ev": ev, "libro": libro,
                })

    # ---- Totales (Over/Under) — ambos lados forzados a la MISMA linea (consenso) ----
    linea = _linea_total_consenso(odds_evento)
    over = _mejor_precio_linea(odds_evento, "totals", "Over", linea) if linea is not None else None
    under = _mejor_precio_linea(odds_evento, "totals", "Under", linea) if linea is not None else None
    if over and under:
        p_over = _prob_over_interp(overs, linea)
        do = americano_a_decimal(over[0])
        du = americano_a_decimal(under[0])
        fair_o, fair_u = devig_dos_vias(decimal_a_prob(do), decimal_a_prob(du))
        for lado, p_mod, dec, precio, fair in [
            ("Over", p_over, do, over[0], fair_o),
            ("Under", 1 - p_over, du, under[0], fair_u),
        ]:
            ev = ev_por_unidad(p_mod, dec)
            if ev > UMBRAL_EV:
                jugadas.append({
                    "mercado": "TOTAL", "pick": lado, "linea": linea,
                    "p_modelo": p_mod, "p_mercado": fair,
                    "momio": precio, "ev": ev, "libro": over[2] if lado == "Over" else under[2],
                })

    return jugadas

def formato_jugada(j):
    signo = "+" if j["momio"] > 0 else ""
    linea = f" {j['linea']}" if j["linea"] != "" else ""
    return (f"  💰 VALOR {j['mercado']} {j['pick']}{linea} @ {signo}{j['momio']} ({j['libro']}) | "
            f"modelo {j['p_modelo']:.1%} vs mercado {j['p_mercado']:.1%} | EV +{j['ev']:.1%}")
