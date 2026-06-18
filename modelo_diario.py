# ============================================================
# SPRINT 5 — MODELO DIARIO
# Hereda de Sprint 4 (binomial negativa + lambdas amortiguadas) y agrega:
#   #2 Deteccion de valor (+EV) contra la linea real      -> valor.py
#   #3 Ofensiva pulida: reciencia + splits vs zurdo/derecho
#   #4 Bullpen dinamico (ERA de relevistas, automatico)
#   #6 Robustez: RNG unico, cache, manejo de errores, anti-duplicados
# ============================================================

import statsapi
import numpy as np
import os
import sys
from datetime import date

import valor  # modulo #2

# #6: consola de Windows en UTF-8 para que los emojis/acentos no truenen
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------- CONSTANTES ----------------
LIGA_FIP = 4.15
HFA = 1.04
N_SIMS = 10_000
LINEAS = [5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5]
TEMPORADA = 2026
INICIO_TEMP = "03/25/2026"

# --- PARAMETROS DE CALIBRACION (las perillas del Sprint 4) ---
AMORTIGUA = 0.6      # 0 = ignora el pitcheo; 1 = efecto completo. 0.6 suaviza extremos
DISPERSION_K = 4.0   # dispersion de la binomial negativa. Mas bajo = mas varianza/caos
AJUSTE_BASE = 0.94   # calibra el nivel global de carreras para promediar ~8.5 el slate

# --- PARAMETROS NUEVOS (Sprint 5) ---
SEMILLA = None       # pon un entero para resultados reproducibles (backtests)
VIDA_MEDIA = 20      # juegos: peso de reciencia de la ofensiva (half-life)
SPLIT_TOPE = 0.15    # +/-15% maximo que el split L/R puede mover una ofensiva

RNG = np.random.default_rng(SEMILLA)  # #6: un solo generador, no uno por llamada

PARK = {
    "Colorado Rockies": 1.20, "Boston Red Sox": 1.06, "Cincinnati Reds": 1.05,
    "Athletics": 1.07, "Arizona Diamondbacks": 1.04, "New York Yankees": 1.03,
    "Philadelphia Phillies": 1.03, "Chicago White Sox": 1.03, "Kansas City Royals": 1.02,
    "Texas Rangers": 1.01, "Atlanta Braves": 1.01, "Chicago Cubs": 1.00,
    "Minnesota Twins": 1.00, "Washington Nationals": 1.00, "Toronto Blue Jays": 1.00,
    "Milwaukee Brewers": 1.00, "Los Angeles Angels": 0.99, "Houston Astros": 0.99,
    "St. Louis Cardinals": 0.98, "Pittsburgh Pirates": 0.98, "Los Angeles Dodgers": 0.98,
    "Baltimore Orioles": 0.98, "Detroit Tigers": 0.98, "Cleveland Guardians": 0.98,
    "New York Mets": 0.97, "Miami Marlins": 0.96, "Tampa Bay Rays": 0.96,
    "San Diego Padres": 0.94, "Seattle Mariners": 0.92, "San Francisco Giants": 0.92,
}

# Override manual del bullpen (opcional). Si un equipo no esta aqui, se calcula solo.
BULLPEN_OVERRIDE = {}

# ---------------- HERRAMIENTAS ----------------

def ip_a_decimal(ip_texto):
    entero, _, fraccion = str(ip_texto).partition(".")
    return int(entero) + int(fraccion or 0) / 3

def calcular_fip(hr, bb, hbp, k, ip):
    if ip == 0:
        return None
    return (13*hr + 3*(bb + hbp) - 2*k) / ip + 3.17

def prob_a_momio(p):
    if p <= 0.01 or p >= 0.99:
        return "—"
    if p >= 0.5:
        return f"{-round(100 * p / (1 - p))}"
    return f"+{round(100 * (1 - p) / p)}"

# --- caches (#6) ---
cache_pitcher = {}   # nombre -> (fip, ip_esp, mano)
cache_equipo = {}    # nombre -> rs_ponderado
cache_bullpen = {}   # nombre -> era_relevistas
cache_split = {}     # (nombre, mano) -> multiplicador
_cache_tid = {}      # nombre -> team id

def _team_id(nombre):
    if nombre not in _cache_tid:
        eq = statsapi.lookup_team(nombre)
        _cache_tid[nombre] = eq[0]["id"] if eq else None
    return _cache_tid[nombre]

def datos_pitcher(nombre):
    """Devuelve (fip, innings_esperados, mano 'L'/'R'). Cacheado y a prueba de fallos."""
    if nombre in cache_pitcher:
        return cache_pitcher[nombre]
    try:
        res = statsapi.lookup_player(nombre)
        if not res:
            return _guardar_pitcher(nombre, (None, None, None))
        pid = res[0]["id"]

        persona = statsapi.get("person", {"personId": pid})
        mano = persona["people"][0].get("pitchHand", {}).get("code")

        data = statsapi.player_stat_data(pid, group="pitching", type="season")
        if not data["stats"]:
            return _guardar_pitcher(nombre, (None, None, mano))
        s = data["stats"][0]["stats"]
        ip_temp = ip_a_decimal(s.get("inningsPitched", 0))
        fip = calcular_fip(s.get("homeRuns", 0), s.get("baseOnBalls", 0),
                           s.get("hitByPitch", 0), s.get("strikeOuts", 0), ip_temp)

        log = statsapi.player_stat_data(pid, group="pitching", type="gameLog")
        ips = [ip_a_decimal(g["stats"].get("inningsPitched", 0)) for g in log["stats"][-3:]]
        ip_esp = sum(ips) / len(ips) if ips else 5.0
        ip_esp = max(3.5, min(ip_esp, 7.0))
        return _guardar_pitcher(nombre, (fip, ip_esp, mano))
    except Exception as e:
        print(f"⚠ Error con pitcher {nombre}: {e}")
        return _guardar_pitcher(nombre, (None, None, None))

def _guardar_pitcher(nombre, valor_t):
    cache_pitcher[nombre] = valor_t
    return valor_t

def carreras_por_juego(nombre_equipo, hoy):
    """#3: carreras/juego PONDERADAS por reciencia (half-life = VIDA_MEDIA juegos)."""
    if nombre_equipo in cache_equipo:
        return cache_equipo[nombre_equipo]
    tid = _team_id(nombre_equipo)
    if tid is None:
        return 4.4
    try:
        temporada = statsapi.schedule(start_date=INICIO_TEMP, end_date=hoy, team=tid)
    except Exception:
        return 4.4
    anotadas = [j["home_score"] if j["home_id"] == tid else j["away_score"]
                for j in temporada if j["status"] == "Final"]
    if not anotadas:
        return 4.4
    # los juegos vienen en orden cronologico: el ultimo es el mas reciente
    n = len(anotadas)
    pesos = [0.5 ** ((n - 1 - i) / VIDA_MEDIA) for i in range(n)]
    rg = sum(p * r for p, r in zip(pesos, anotadas)) / sum(pesos)
    cache_equipo[nombre_equipo] = rg
    return rg

def bullpen_era(nombre_equipo):
    """#4: ERA real de los relevistas del equipo (split 'rp'). Automatico + cache."""
    if nombre_equipo in BULLPEN_OVERRIDE:
        return BULLPEN_OVERRIDE[nombre_equipo]
    if nombre_equipo in cache_bullpen:
        return cache_bullpen[nombre_equipo]
    tid = _team_id(nombre_equipo)
    era = 4.15
    if tid is not None:
        try:
            d = statsapi.get("team_stats", {"teamId": tid, "stats": "statSplits",
                                            "group": "pitching", "season": TEMPORADA,
                                            "sitCodes": "rp", "gameType": "R"})
            sp = d["stats"][0]["splits"]
            if sp:
                era = float(sp[0]["stat"].get("era", 4.15))
        except Exception:
            pass
    cache_bullpen[nombre_equipo] = era
    return era

def split_ofensivo(nombre_equipo, mano):
    """#3: multiplicador de la ofensiva segun la mano del abridor rival (OPS vs LHP/RHP)."""
    if mano not in ("L", "R"):
        return 1.0
    clave = (nombre_equipo, mano)
    if clave in cache_split:
        return cache_split[clave]
    tid = _team_id(nombre_equipo)
    mult = 1.0
    if tid is not None:
        sit = "vl" if mano == "L" else "vr"
        try:
            base = statsapi.get("team_stats", {"teamId": tid, "stats": "season",
                                               "group": "hitting", "season": TEMPORADA,
                                               "gameType": "R"})
            ops_base = float(base["stats"][0]["splits"][0]["stat"].get("ops", 0) or 0)
            vs = statsapi.get("team_stats", {"teamId": tid, "stats": "statSplits",
                                             "group": "hitting", "season": TEMPORADA,
                                             "sitCodes": sit, "gameType": "R"})
            sp = vs["stats"][0]["splits"]
            if sp and ops_base > 0:
                ops_vs = float(sp[0]["stat"].get("ops", 0) or 0)
                if ops_vs > 0:
                    crudo = ops_vs / ops_base
                    mult = max(1 - SPLIT_TOPE, min(crudo, 1 + SPLIT_TOPE))
        except Exception:
            pass
    cache_split[clave] = mult
    return mult

def fip_combinado(fip_abridor, ip_abridor, era_bullpen):
    return (fip_abridor * ip_abridor + era_bullpen * (9 - ip_abridor)) / 9

def multiplicador_pitcheo(fip_comb):
    """ARREGLO 2: amortigua el efecto del pitcheo hacia 1.0 en vez de lineal puro."""
    crudo = fip_comb / LIGA_FIP
    return 1 + AMORTIGUA * (crudo - 1)

def simular_binom_neg(lam, n):
    """ARREGLO 1: marcadores con binomial negativa (varianza > media)."""
    p = DISPERSION_K / (DISPERSION_K + lam)
    return RNG.negative_binomial(DISPERSION_K, p, n)

def simular(lam_v, lam_c):
    c_v = simular_binom_neg(lam_v, N_SIMS)
    c_c = simular_binom_neg(lam_c, N_SIMS)
    tot = c_v + c_c
    empates = c_c == c_v
    moneda = RNG.random(N_SIMS) < (lam_c / (lam_c + lam_v))
    gana_c = (c_c > c_v) | (empates & moneda)
    overs = {ln: (tot > ln).mean() for ln in LINEAS}
    return overs, gana_c.mean(), ((c_c - c_v) >= 2).mean()

# ---------------- PROCESO PRINCIPAL ----------------

def correr(fecha=None):
    """Corre el modelo para una fecha (mm/dd/YYYY). Sin argumento usa hoy."""
    hoy = fecha or date.today().strftime("%m/%d/%Y")
    juegos = statsapi.schedule(date=hoy)
    modelables = [j for j in juegos if j["status"] in ("Scheduled", "Pre-Game", "Warmup")
                  and j["away_probable_pitcher"] and j["home_probable_pitcher"]]

    odds_slate = valor.obtener_odds()  # #2: {} si no hay ODDS_API_KEY

    print(f"=== MODELO DIARIO v3 — {hoy} ===")
    print(f"Calibracion: amortigua={AMORTIGUA} | dispersion_k={DISPERSION_K} | base={AJUSTE_BASE}")
    print(f"Modelables: {len(modelables)} | Lineas de mercado: {'si' if odds_slate else 'no (sin ODDS_API_KEY)'}\n")

    filas_csv = []
    totales_slate = []
    jugadas_valor = []

    for j in modelables:
        visita, casa = j["away_name"], j["home_name"]
        p_v, p_c = j["away_probable_pitcher"], j["home_probable_pitcher"]
        print(f"--- {visita} @ {casa} ---")
        print(f"Abridores: {p_v} vs {p_c}")

        fip_v, ip_v, mano_v = datos_pitcher(p_v)
        fip_c, ip_c, mano_c = datos_pitcher(p_c)
        if fip_v is None or fip_c is None:
            print("⚠ Sin datos — juego omitido\n")
            continue

        rg_v = carreras_por_juego(visita, hoy)
        rg_c = carreras_por_juego(casa, hoy)
        park = PARK.get(casa, 1.00)

        # #3: la ofensiva se ajusta a la mano del abridor que enfrenta
        split_v = split_ofensivo(visita, mano_c)
        split_c = split_ofensivo(casa, mano_v)

        # #4: bullpen real, ya no fijo en 4.15
        pitcheo_c = fip_combinado(fip_c, ip_c, bullpen_era(casa))
        pitcheo_v = fip_combinado(fip_v, ip_v, bullpen_era(visita))

        lam_v = rg_v * split_v * multiplicador_pitcheo(pitcheo_c) * park * AJUSTE_BASE
        lam_c = rg_c * split_c * multiplicador_pitcheo(pitcheo_v) * park * AJUSTE_BASE * HFA

        overs, p_casa, p_casa_rl = simular(lam_v, lam_c)
        totales_slate.append(lam_v + lam_c)

        print(f"Inputs: {p_v} FIP {fip_v:.2f} ({ip_v:.1f}IP, {mano_v or '?'}) | "
              f"{p_c} FIP {fip_c:.2f} ({ip_c:.1f}IP, {mano_c or '?'})")
        print(f"Bullpens: {visita} {bullpen_era(visita):.2f} | {casa} {bullpen_era(casa):.2f}")
        print(f"Ofensivas: {visita} {rg_v:.2f}x{split_v:.2f} | {casa} {rg_c:.2f}x{split_c:.2f} R/G | Park {park}")
        print(f"Carreras esp: {visita} {lam_v:.2f} — {casa} {lam_c:.2f} (total {lam_v+lam_c:.2f})")
        print(f"ML: {casa} {p_casa:.1%} ({prob_a_momio(p_casa)}) | {visita} {1-p_casa:.1%} ({prob_a_momio(1-p_casa)})")
        print(f"RL: {casa} -1.5 {p_casa_rl:.1%} | {visita} +1.5 {1-p_casa_rl:.1%}")
        print("Overs:  " + " | ".join(f"O{ln} {p:.0%}" for ln, p in overs.items()))
        print("Unders: " + " | ".join(f"U{ln} {1-p:.0%}" for ln, p in overs.items()))

        # #2: valor contra el mercado
        jugadas = valor.analizar_juego(odds_slate.get((visita, casa)), visita, casa, p_casa, overs)
        for jg in jugadas:
            print(valor.formato_jugada(jg))
            jugadas_valor.append((visita, casa, jg))
        print()

        filas_csv.append(",".join([
            hoy, visita, casa, p_v, p_c,
            f"{lam_v:.2f}", f"{lam_c:.2f}", f"{lam_v+lam_c:.2f}",
            f"{p_casa:.3f}", f"{overs[7.5]:.3f}", f"{overs[8.5]:.3f}", f"{overs[9.5]:.3f}"
        ]))

    if totales_slate:
        print(f"📊 Total promedio del slate: {np.mean(totales_slate):.2f} (objetivo ~8.5)")
    if odds_slate:
        print(f"💰 Jugadas con valor (+EV > {valor.UMBRAL_EV:.0%}): {len(jugadas_valor)}")

    # ---------------- GUARDADO (anti-duplicados, #6) ----------------

    archivo = "predicciones.csv"
    nuevo = not os.path.exists(archivo)
    existentes = set()
    if not nuevo:
        with open(archivo, encoding="utf-8") as f:
            for linea in f.readlines()[1:]:
                partes = linea.split(",")
                if len(partes) >= 3:
                    existentes.add((partes[0], partes[1], partes[2]))

    guardadas = 0
    with open(archivo, "a", encoding="utf-8") as f:
        if nuevo:
            f.write("fecha,visita,casa,abridor_v,abridor_c,lam_v,lam_c,total_esp,p_casa,p_over75,p_over85,p_over95\n")
        for fila in filas_csv:
            partes = fila.split(",")
            clave = (partes[0], partes[1], partes[2])
            if clave in existentes:
                continue
            f.write(fila + "\n")
            existentes.add(clave)
            guardadas += 1

    omitidas = len(filas_csv) - guardadas
    print(f"✅ {guardadas} predicciones guardadas en {archivo}" +
          (f" ({omitidas} duplicadas omitidas)" if omitidas else ""))


if __name__ == "__main__":
    import sys as _sys
    correr(_sys.argv[1] if len(_sys.argv) > 1 else None)
