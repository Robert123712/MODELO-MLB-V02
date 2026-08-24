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
from datetime import date, datetime

import valor  # modulo #2

# #6: consola de Windows en UTF-8 para que los emojis/acentos no truenen
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------- CONSTANTES ----------------
LIGA_FIP = 4.15
# Ventaja de local REPARTIDA: la casa anota x HFA y la visita anota / HFA.
# Antes solo inflaba la ofensiva local y el modelo daba 51.9% de victoria local
# cuando la realidad de 552 juegos validados fue 54.5% (la liga historica anda
# en ~54%). Repartirla dobla el efecto sobre el ML sin mover el total del juego,
# que ya estaba bien calibrado (bias -0.08). Calibrado a p_casa ~54%.
HFA = 1.045
N_SIMS = 50_000
LINEAS = [5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5]
LINEAS_F5 = [3.5, 4.5, 5.5]          # totales de las primeras 5 entradas
TEMPORADA = 2026
INICIO_TEMP = "03/25/2026"

# --- PARAMETROS DE CALIBRACION (las perillas del Sprint 4) ---
AMORTIGUA = 0.6      # 0 = ignora el pitcheo; 1 = efecto completo. 0.6 suaviza extremos
DISPERSION_K = 4.0   # dispersion de la binomial negativa. Mas bajo = mas varianza/caos
DISPERSION_K_F5 = 2.4  # F5: menos entradas -> mas varianza relativa -> k mas bajo
AJUSTE_BASE = 0.94   # calibra el nivel global de carreras para promediar ~8.5 el slate
# FRAC_F5 se calcula dinamicamente desde datos reales de la temporada
# (carreras en innings 1-5 / carreras totales del juego)
# El calculo se hace una vez y se cachea. Fallback si no hay datos: 0.62
FRAC_F5 = None
_cache_f5_frac = None

# --- PARAMETROS NUEVOS (Sprint 5+) ---
SEMILLA = None       # pon un entero para resultados reproducibles (backtests)
VIDA_MEDIA = 20      # juegos: peso de reciencia de la ofensiva (half-life)
PESO_OFENSIVA_RECIENTE = 0.45  # mezcla de la ofensiva: 45% reciencia / 55% temporada
                               # completa. Reciencia pura persigue rachas de bateo
                               # (mayormente ruido) y peleaba MLs contra el mercado
SPLIT_TOPE = 0.15    # +/-15% maximo que el split L/R puede mover una ofensiva
PESO_FIP_RECIENTE = 0.35  # peso MAXIMO del FIP reciente; se escala por las IP que traiga
SHRINK_IP = 60       # IP de regresion: el FIP de temporada se encoge hacia la liga segun muestra
SHRINK_IP_REC = 20   # IP a las que el FIP reciente gana la mitad de su peso maximo

# --- NIVEL DE REEMPLAZO (abridor sin stats de la temporada) ---
# Peor que la liga (4.15) a proposito: quien no tiene historial reciente en MLB
# —debutante, regreso de lesion, subida de ligas menores— rinde por debajo del
# promedio. Sale temprano por limite de pitcheos, de ahi las IP bajas.
FIP_REEMPLAZO = 4.80
IP_REEMPLAZO = 4.5
PARK_PITCHER_TOPE = 0.08   # +/-8%: tope de la correccion por parques donde lanzo
LIGA_K9 = 8.80       # ponches por 9 innings (promedio MLB 2025-26)
LIGA_BB9 = 3.20      # bases por bolas por 9 innings

# --- PARAMETROS DE PREDICCION DE HITS ---
LIGA_AVG = 0.248     # promedio de bateo de la liga
LIGA_BABIP = 0.290   # BABIP promedio de la liga
PA_POR_ORDEN = {1: 4.60, 2: 4.45, 3: 4.35, 4: 4.25, 5: 4.15, 6: 4.05, 7: 3.95, 8: 3.85, 9: 3.70} # PA dinamico

import threading

# #6+: RNG por hilo. app.py simula juegos en paralelo (ThreadPool) y el
# Generator de numpy NO es thread-safe: compartir uno solo entre hilos puede
# corromper el estado del bit-generator. Cada hilo recibe el suyo, derivado de
# SEMILLA (si se fijo) para mantener reproducibilidad en backtests.
_rng_local = threading.local()

def _rng():
    gen = getattr(_rng_local, "gen", None)
    if gen is None:
        seed = None if SEMILLA is None else SEMILLA + threading.get_ident()
        gen = np.random.default_rng(seed)
        _rng_local.gen = gen
    return gen

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

# ---------------- MOMIOS: COMO SE COMPORTAN LAS LINEAS REALES ----------------
# Tres reglas del mercado que el modelo tiene que respetar:
#
# 1) "-100" NO EXISTE. En momio americano el negativo dice cuanto arriesgas para
#    ganar 100, asi que -100 seria "arriesga 100 para ganar 100": exactamente lo
#    mismo que +100. Las casas lo rotulan EVEN (o PK), nunca -100.
#
# 2) LAS CASAS NO PUBLICAN EL PRECIO JUSTO. Le cargan su margen (vig): las
#    probabilidades implicadas de los dos lados suman ~104-105%, no 100%. Por eso
#    un juego 50/50 se postea -110/-110 y NO EVEN/EVEN. Consecuencia practica:
#    el momio justo del modelo casi siempre se ve "mejor" que el del casino, y
#    compararlos de frente engania. Hay que compararlo contra la linea CON vig
#    (momio_mercado) o de-viguear la del casino (eso hace valor.py).
#
# 3) LOS MOMIOS VAN EN ESCALONES. Ninguna casa postea -107 en un moneyline: se
#    ven -105, -110, -115, -120..., y el escalon crece con el precio.

VIG_TIPICO = 0.045   # sobre-redondez tipica de un mercado a 2 vias de MLB (~4.5%)


def _momio_crudo(p):
    """Magnitud del momio americano (siempre >= 100) para una probabilidad."""
    return 100 * p / (1 - p) if p >= 0.5 else 100 * (1 - p) / p


def _escalon_momio(m):
    """Redondea a los escalones que las casas usan de verdad: 5 cerca del pick,
    10 en favoritos medianos, 25 cuando el precio se dispara."""
    paso = 5 if m < 200 else (10 if m < 400 else 25)
    return int(round(m / paso) * paso)


def prob_a_momio(p):
    """Momio JUSTO (sin vig): el precio que hace la apuesta neutral.
    Es el punto de referencia contra el que se mide la casa, no un precio que
    alguien vaya a ofrecer. Devuelve EVEN en vez de -100 (ver regla 1)."""
    if p <= 0.01 or p >= 0.99:
        return "—"
    m = round(_momio_crudo(p))
    if m == 100:
        return "EVEN"
    return f"-{m}" if p >= 0.5 else f"+{m}"


def momio_mercado(p, vig=VIG_TIPICO):
    """Lo que una casa POSTEARIA realmente: el precio justo mas su margen,
    redondeado a escalones reales. Este es el numero que hay que comparar
    contra tu casino: si te ofrecen MEJOR que esto hay valor; si te ofrecen
    peor, te estan cobrando mas vig de la cuenta.

    Nota: en mercados a 3 vias (ML de F5) el margen real suele ser mayor que
    este 4.5%, asi que ahi la linea estimada queda algo conservadora."""
    if p <= 0.01 or p >= 0.99:
        return "—"
    # La casa infla la probabilidad implicada de CADA lado (por eso los dos
    # lados de un juego parejo salen negativos: -110/-110), pero NO por igual:
    # carga el margen sobre el NO FAVORITO. Repartirlo proporcionalmente
    # exageraba a los favoritos grandes (un 90% daba -1575 cuando el mercado
    # real postea ~-950). Repartirlo segun (1-p) reproduce lineas reales:
    # 75% -> -320/+250, 60% -> -160/+135, justo lo que se ve en un tablero.
    p_vig = min(p + vig * (1 - p), 0.98)
    m = _escalon_momio(_momio_crudo(p_vig))
    if m == 100:
        return "EVEN"
    return f"-{m}" if p_vig >= 0.5 else f"+{m}"

# --- caches (#6) ---
cache_pitcher = {}   # nombre -> dict con fip, ip_esp, mano, k9, bb9, fip_reciente
cache_equipo = {}    # nombre -> rs_ponderado
cache_bullpen = {}   # nombre -> dict {fip, era, k9, bb9}
cache_split = {}     # (nombre, mano) -> multiplicador
_cache_tid = {}      # nombre -> team id
_cache_defensa = {}  # nombre -> factor defensivo
_cache_team_schedule = {}  # (tid, hoy) -> schedule list (compartido entre funciones)

def _team_id(nombre):
    if nombre not in _cache_tid:
        eq = statsapi.lookup_team(nombre)
        _cache_tid[nombre] = eq[0]["id"] if eq else None
    return _cache_tid[nombre]

def _team_schedule(tid, hoy):
    """Cache compartido de schedule por equipo. Evita llamadas duplicadas entre funciones."""
    key = (tid, hoy)
    if key not in _cache_team_schedule:
        try:
            _cache_team_schedule[key] = statsapi.schedule(start_date=INICIO_TEMP, end_date=hoy, team=tid)
        except Exception:
            _cache_team_schedule[key] = []
    return _cache_team_schedule[key]

def datos_pitcher(nombre):
    """Devuelve dict con fip, ip_esp, mano, k9, bb9, fip_reciente. Cacheado."""
    if nombre in cache_pitcher:
        return cache_pitcher[nombre]
    try:
        res = statsapi.lookup_player(nombre)
        if not res:
            return _guardar_pitcher(nombre, None)
        pid = res[0]["id"]

        persona = statsapi.get("person", {"personId": pid})
        mano = persona["people"][0].get("pitchHand", {}).get("code")

        data = statsapi.get("person", {"personId": pid, "hydrate": "stats(group=[pitching],type=[season])"})
        people = data.get("people", [])
        if not people:
            return _guardar_pitcher(nombre, {"fip": None, "ip_esp": None, "mano": mano, "k9": None, "bb9": None, "baa": None, "fip_reciente": None})
        stats_arr = people[0].get("stats", [])
        season_stats = None
        for g in stats_arr:
            if g.get("group", {}).get("displayName") == "pitching" and g.get("type", {}).get("displayName") == "season":
                splits = g.get("splits", [])
                if splits:
                    season_stats = splits[0]["stat"]
                    break
        if not season_stats:
            return _guardar_pitcher(nombre, {"fip": None, "ip_esp": None, "mano": mano, "k9": None, "bb9": None, "baa": None, "fip_reciente": None})

        s = season_stats
        ip_temp = ip_a_decimal(s.get("inningsPitched", 0))
        k = s.get("strikeOuts", 0) or 0
        bb = s.get("baseOnBalls", 0) or 0
        hr = s.get("homeRuns", 0) or 0
        hbp = s.get("hitByPitch", 0) or 0
        baa_raw = s.get("avg", None)

        fip = calcular_fip(hr, bb, hbp, k, ip_temp)
        k9 = k * 9 / ip_temp if ip_temp > 0 else 0
        bb9 = bb * 9 / ip_temp if ip_temp > 0 else 0
        baa = float(baa_raw) if baa_raw else None

        log_data = statsapi.get("person", {"personId": pid, "hydrate": "stats(group=[pitching],type=[gameLog])"})
        log_people = log_data.get("people", [])
        log_splits = []
        if log_people:
            for g in log_people[0].get("stats", []):
                if g.get("group", {}).get("displayName") == "pitching" and g.get("type", {}).get("displayName") == "gameLog":
                    log_splits = g.get("splits", [])
                    break
        ips = [ip_a_decimal(g["stat"].get("inningsPitched", 0)) for g in log_splits[-3:]]
        ip_esp = sum(ips) / len(ips) if ips else 5.0
        ip_esp = max(3.5, min(ip_esp, 7.0))

        # Park factor promedio de los estadios donde lanzo, ponderado por IP.
        # La ofensiva ya se park-ajusta; el pitcheo no lo hacia, asi que el FIP
        # de un abridor de Colorado se leia como falta de talento y el de uno de
        # San Francisco como sobra. El gameLog ya esta descargado: sale gratis.
        park_ip, ip_total_log = 0.0, 0.0
        for g in log_splits:
            ip_j = ip_a_decimal(g["stat"].get("inningsPitched", 0))
            if ip_j <= 0:
                continue
            propio = (g.get("team") or {}).get("name")
            rival = (g.get("opponent") or {}).get("name")
            sede = propio if g.get("isHome") else rival
            park_ip += PARK.get(sede, 1.00) * ip_j
            ip_total_log += ip_j
        park_medio = park_ip / ip_total_log if ip_total_log > 0 else 1.00
        # Tope: con pocas aperturas el promedio puede quedar en un extremo y la
        # correccion se volveria mas ruido que senal.
        park_medio = max(1 - PARK_PITCHER_TOPE, min(park_medio, 1 + PARK_PITCHER_TOPE))

        # FIP reciente: ultimas 3 salidas con >= 1 IP, ponderado por IP de cada
        # salida (agregar los innings es el pooling correcto; el promedio simple
        # dejaba que una salida de 1.2 IP pesara igual que una de 7)
        fips_recientes = []   # (fip_salida, ip_salida)
        for g in log_splits[-3:]:
            gs = g["stat"]
            ip_j = ip_a_decimal(gs.get("inningsPitched", 0))
            if ip_j >= 1.0:
                fj = calcular_fip(
                    gs.get("homeRuns", 0) or 0, gs.get("baseOnBalls", 0) or 0,
                    gs.get("hitByPitch", 0) or 0, gs.get("strikeOuts", 0) or 0, ip_j
                )
                if fj is not None:
                    fips_recientes.append((fj, ip_j))
        ip_reciente = sum(ipj for _, ipj in fips_recientes)
        fip_reciente = (sum(fj * ipj for fj, ipj in fips_recientes) / ip_reciente
                        if ip_reciente > 0 else None)

        return _guardar_pitcher(nombre, {
            "fip": fip, "ip_esp": ip_esp, "mano": mano,
            "k9": k9, "bb9": bb9, "baa": baa,
            "fip_reciente": fip_reciente,
            "ip_temp": ip_temp, "ip_reciente": ip_reciente,
            "park_medio": park_medio,
        })
    except Exception as e:
        print(f"⚠ Error con pitcher {nombre}: {e}")
        return _guardar_pitcher(nombre, None)

def _guardar_pitcher(nombre, valor):
    cache_pitcher[nombre] = valor
    return valor

def fip_blend(p):
    """FIP con encogimiento por tamano de muestra (shrinkage bayesiano simple).

    1) El FIP de temporada regresa hacia LIGA_FIP segun sus IP: con pocas
       entradas el numero es ruido y se le cree poco; con ~150+ IP domina.
         fip_temp = (FIP*IP + LIGA_FIP*SHRINK_IP) / (IP + SHRINK_IP)
    2) El FIP reciente (ultimas 3 salidas, ~15 IP) NO tiene peso fijo: gana
       peso segun los innings que traiga, con tope PESO_FIP_RECIENTE.
         w = PESO_FIP_RECIENTE * ip_rec / (ip_rec + SHRINK_IP_REC)
       (3 salidas completas ~18 IP -> w~0.17; una apertura corta casi no mueve)
    """
    if p is None or p["fip"] is None:
        return LIGA_FIP
    ip = p.get("ip_temp") or 0.0
    if ip <= 0:
        # Sin innings en la temporada: el FIP que traiga YA es una estimacion
        # (nivel de reemplazo), no una medicion. Encogerlo lo devolveria a la
        # media de liga y borraria justo la penalizacion que queremos aplicar.
        return p["fip"]
    fip_temp = (p["fip"] * ip + LIGA_FIP * SHRINK_IP) / (ip + SHRINK_IP)
    if p.get("fip_reciente") is None:
        return _park_neutral(fip_temp, p)
    ip_rec = p.get("ip_reciente") or 0.0
    w = PESO_FIP_RECIENTE * ip_rec / (ip_rec + SHRINK_IP_REC)
    return _park_neutral(fip_temp * (1 - w) + p["fip_reciente"] * w, p)

# ---------------- CLIMA ----------------
# El aire caliente es menos denso: la pelota vuela mas lejos. Es fisica, no
# folclor — la distancia de un batazo crece ~0.5-0.8% por cada 10 grados F.
#
# NO se modela el viento: su efecto depende de la ORIENTACION del estadio
# (a favor en Wrigley vale ~1.5 carreras, en contra las quita) y no tenemos
# los rumbos home->jardin central verificados. Meterlo con orientaciones
# aproximadas seria peor que no meterlo: ruido con cara de senal.

TEMP_REF_F = 70.0        # temperatura de referencia
TEMP_PESO = 0.004        # ~+4% de carreras por cada 10 grados F arriba
TEMP_TOPE = 0.05         # +/-5% maximo

# Estadios con techo (retractil o fijo): el clima exterior no aplica.
# Con techo retractil se asume cerrado, que es lo comun cuando hay frio o lluvia.
TECHO = {
    "Tampa Bay Rays", "Toronto Blue Jays", "Milwaukee Brewers", "Houston Astros",
    "Texas Rangers", "Arizona Diamondbacks", "Miami Marlins", "Seattle Mariners",
}

# lat/lon de cada sede, para pedirle el pronostico a Open-Meteo (gratis, sin key)
SEDES = {
    "Arizona Diamondbacks": (33.445, -112.067), "Atlanta Braves": (33.891, -84.468),
    "Baltimore Orioles": (39.284, -76.622), "Boston Red Sox": (42.346, -71.097),
    "Chicago Cubs": (41.948, -87.655), "Chicago White Sox": (41.830, -87.634),
    "Cincinnati Reds": (39.097, -84.507), "Cleveland Guardians": (41.496, -81.685),
    "Colorado Rockies": (39.756, -104.994), "Detroit Tigers": (42.339, -83.049),
    "Houston Astros": (29.757, -95.355), "Kansas City Royals": (39.051, -94.480),
    "Los Angeles Angels": (33.800, -117.883), "Los Angeles Dodgers": (34.074, -118.240),
    "Miami Marlins": (25.778, -80.220), "Milwaukee Brewers": (43.028, -87.971),
    "Minnesota Twins": (44.982, -93.278), "New York Mets": (40.757, -73.846),
    "New York Yankees": (40.829, -73.926), "Athletics": (39.541, -119.802),
    "Philadelphia Phillies": (39.906, -75.166), "Pittsburgh Pirates": (40.447, -80.006),
    "San Diego Padres": (32.707, -117.157), "San Francisco Giants": (37.778, -122.389),
    "Seattle Mariners": (47.591, -122.332), "St. Louis Cardinals": (38.622, -90.193),
    "Tampa Bay Rays": (27.768, -82.653), "Texas Rangers": (32.747, -97.084),
    "Toronto Blue Jays": (43.641, -79.389), "Washington Nationals": (38.873, -77.007),
}

_cache_clima = {}


def factor_clima(nombre_casa, hoy):
    """Multiplicador de carreras por temperatura. 1.0 con techo o sin datos."""
    if nombre_casa in TECHO:
        return 1.0
    clave = (nombre_casa, hoy)
    if clave in _cache_clima:
        return _cache_clima[clave]
    coords = SEDES.get(nombre_casa)
    factor = 1.0
    if coords:
        try:
            import json as _json
            import urllib.request
            mm, dd, yyyy = hoy.split("/")
            lat, lon = coords
            url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
                   f"&daily=temperature_2m_max&temperature_unit=fahrenheit"
                   f"&start_date={yyyy}-{mm}-{dd}&end_date={yyyy}-{mm}-{dd}&timezone=auto")
            with urllib.request.urlopen(url, timeout=10) as r:
                data = _json.load(r)
            temps = data.get("daily", {}).get("temperature_2m_max") or []
            if temps and temps[0] is not None:
                # el maximo diario aproxima la temperatura de un juego de tarde/noche
                factor = 1 + TEMP_PESO * (float(temps[0]) - TEMP_REF_F)
                factor = max(1 - TEMP_TOPE, min(factor, 1 + TEMP_TOPE))
        except Exception as e:
            print(f"⚠ Clima de {nombre_casa}: sin datos, se usa neutro ({e})")
    _cache_clima[clave] = factor
    return factor


# ---------------- FATIGA DEL BULLPEN ----------------
# El modelo usaba el FIP de temporada del bullpen, pero un bullpen que tiro 6
# entradas ayer NO es el mismo hoy: los mejores brazos no estan disponibles y
# el manager tiene que usar al relevista largo. Se mide con las entradas de
# relevo de los ultimos 2 dias.

BULLPEN_IP_TIPICO = 3.4    # entradas de relevo en un juego normal
FATIGA_PESO = 0.030        # cuanto degrada el FIP cada entrada de exceso
FATIGA_TOPE = 0.07         # +/-7% maximo

_cache_boxscore = {}       # game_id -> boxscore (dos equipos comparten llamada)
_cache_fatiga = {}         # (equipo, hoy) -> factor


def _bullpen_ip_juego(game_id, tid):
    """Entradas que tiro el BULLPEN de ese equipo en ese juego (todos menos el
    abridor). None si el boxscore no esta disponible."""
    if game_id not in _cache_boxscore:
        try:
            _cache_boxscore[game_id] = statsapi.get("game_boxscore", {"gamePk": game_id})
        except Exception:
            _cache_boxscore[game_id] = None
    bx = _cache_boxscore[game_id]
    if not bx:
        return None
    for lado in ("home", "away"):
        eq = bx.get("teams", {}).get(lado, {})
        if (eq.get("team") or {}).get("id") != tid:
            continue
        lanzadores = eq.get("pitchers", [])      # en orden de uso: el 1o es el abridor
        jugadores = eq.get("players", {})
        total = 0.0
        for pid in lanzadores[1:]:
            st = jugadores.get(f"ID{pid}", {}).get("stats", {}).get("pitching", {})
            total += ip_a_decimal(st.get("inningsPitched", 0))
        return total
    return None


def factor_fatiga_bullpen(nombre_equipo, hoy):
    """Multiplicador sobre el FIP del bullpen segun su carga reciente.
    > 1 = bullpen quemado (peor). < 1 = descansado."""
    clave = (nombre_equipo, hoy)
    if clave in _cache_fatiga:
        return _cache_fatiga[clave]
    tid = _team_id(nombre_equipo)
    factor = 1.0
    if tid is not None:
        try:
            hoy_d = datetime.strptime(hoy, "%m/%d/%Y").date()
            carga = 0.0
            for j in reversed(_team_schedule(tid, hoy)):
                if j.get("status") != "Final":
                    continue
                dias = (hoy_d - datetime.strptime(j["game_date"], "%Y-%m-%d").date()).days
                if dias < 1:
                    continue
                if dias > 2:
                    break            # el schedule viene en orden: ya no hay nada util
                ip = _bullpen_ip_juego(j["game_id"], tid)
                if ip is None:
                    continue
                carga += ip * (1.0 if dias == 1 else 0.5)
            tipico = BULLPEN_IP_TIPICO * 1.5      # ayer completo + medio antier
            factor = 1 + FATIGA_PESO * (carga - tipico)
            factor = max(1 - FATIGA_TOPE, min(factor, 1 + FATIGA_TOPE))
        except Exception as e:
            print(f"⚠ Fatiga de bullpen de {nombre_equipo}: sin datos ({e})")
    _cache_fatiga[clave] = factor
    return factor


def _park_neutral(fip, p):
    """Lleva el FIP a contexto NEUTRO dividiendo entre el park medio de sus
    aperturas. Despues la lambda vuelve a multiplicar por el park de HOY, asi
    que el estadio entra una sola vez (igual que en la ofensiva)."""
    pm = p.get("park_medio") or 1.00
    return fip / pm if pm > 0 else fip


def carreras_por_juego(nombre_equipo, hoy):
    """#3+: carreras/juego del equipo, con dos correcciones anti-ruido:

    1) PARK-AJUSTADAS: las carreras de cada juego se dividen entre el factor
       del estadio DONDE SE JUGO. Sin esto, media temporada en Coors (1.20)
       se contaba como talento ofensivo (y jugar en Oracle/Petco lo escondia).
    2) MEZCLA temporada/reciencia: la reciencia pura (half-life 20) dejaba que
       las ultimas ~3 semanas fueran ~52% de la senal y el modelo persiguiera
       rachas de bateo. Ahora: 55% promedio de temporada (talento) + 45%
       ponderado por reciencia (lesiones/cambios de roster reales).
    """
    if nombre_equipo in cache_equipo:
        return cache_equipo[nombre_equipo]
    tid = _team_id(nombre_equipo)
    if tid is None:
        return 4.4
    temporada = _team_schedule(tid, hoy)
    if not temporada:
        cache_equipo[nombre_equipo] = 4.4
        return 4.4
    anotadas = []
    for j in temporada:
        if j["status"] != "Final":
            continue
        runs = (j["home_score"] if j["home_id"] == tid else j["away_score"]) or 0
        park_sede = PARK.get(j["home_name"], 1.00)   # el park del equipo LOCAL de ese juego
        anotadas.append(runs / park_sede)
    if not anotadas:
        cache_equipo[nombre_equipo] = 4.4
        return 4.4
    # los juegos vienen en orden cronologico: el ultimo es el mas reciente
    n = len(anotadas)
    pesos = [0.5 ** ((n - 1 - i) / VIDA_MEDIA) for i in range(n)]
    rg_reciente = sum(p * r for p, r in zip(pesos, anotadas)) / sum(pesos)
    rg_temporada = sum(anotadas) / n
    rg = (1 - PESO_OFENSIVA_RECIENTE) * rg_temporada + PESO_OFENSIVA_RECIENTE * rg_reciente
    cache_equipo[nombre_equipo] = rg
    return rg

def _float_seguro(v):
    """La API a veces trae numeros como texto ('3.61') o basura ('-.--')."""
    try:
        f = float(v)
        return f if f == f else None  # descarta NaN
    except (TypeError, ValueError):
        return None

def bullpen_stats(nombre_equipo):
    """#4+: Stats de relevistas: fip, era, k9, bb9 (split 'rp'). Cacheado.

    OJO: la MLB StatsAPI NO expone 'fip' (es metrica de FanGraphs), asi que el
    FIP del bullpen se CALCULA aqui con los componentes del split (HR, BB, HBP,
    K, IP). El codigo anterior hacia st.get('fip', LIGA_FIP) y como el campo no
    existe, los 30 bullpens salian identicos en 4.15."""
    if nombre_equipo in BULLPEN_OVERRIDE:
        return {"fip": BULLPEN_OVERRIDE[nombre_equipo], "era": BULLPEN_OVERRIDE[nombre_equipo], "k9": LIGA_K9, "bb9": LIGA_BB9}
    if nombre_equipo in cache_bullpen:
        return cache_bullpen[nombre_equipo]
    tid = _team_id(nombre_equipo)
    bp = {"fip": LIGA_FIP, "era": LIGA_FIP, "k9": LIGA_K9, "bb9": LIGA_BB9}
    if tid is not None:
        try:
            d = statsapi.get("team_stats", {"teamId": tid, "stats": "statSplits",
                                            "group": "pitching", "season": TEMPORADA,
                                            "sitCodes": "rp", "gameType": "R"})
            sp = d["stats"][0]["splits"]
            if sp:
                st = sp[0]["stat"]
                ip_bp = ip_a_decimal(st.get("inningsPitched", 0))
                era = _float_seguro(st.get("era"))
                if era is not None and era > 0:
                    bp["era"] = era

                fip_calc = None
                if ip_bp > 0:
                    fip_calc = calcular_fip(
                        st.get("homeRuns", 0) or 0,
                        st.get("baseOnBalls", 0) or 0,
                        st.get("hitByPitch", 0) or 0,
                        st.get("strikeOuts", 0) or 0,
                        ip_bp,
                    )
                if fip_calc is not None:
                    bp["fip"] = fip_calc
                elif era is not None and era > 0:
                    bp["fip"] = era   # aproximacion si faltan componentes

                if ip_bp > 0:
                    bp["k9"] = (st.get("strikeOuts", 0) or 0) * 9 / ip_bp
                    bp["bb9"] = (st.get("baseOnBalls", 0) or 0) * 9 / ip_bp
        except Exception as e:
            print(f"⚠ Bullpen de {nombre_equipo}: sin datos, usando promedio de liga ({e})")
    cache_bullpen[nombre_equipo] = bp
    return bp

# ---------------- OFENSIVA SEGUN EL LINEUP DE HOY ----------------
# carreras_por_juego() da el nivel TIPICO del equipo, pero no sabe si hoy
# descansan tres titulares o si volvio el bateador estrella. Este factor mide
# la DESVIACION del lineup de hoy respecto al promedio del equipo, usando el
# OPS de los 9 que si van a jugar, ponderado por turnos segun el puesto en el
# orden. Al ser desviacion (lineup_hoy / equipo_temporada) no duplica el nivel
# del equipo que ya trae rg: solo dice "hoy juegan mejores o peores que su
# promedio". Solo aplica cuando el lineup ya se publico (~1-3h antes).

LINEUP_TOPE = 0.10       # +/-10%: ni el mejor ni el peor lineup mueve mas que esto
_cache_ops_equipo = {}


def ops_equipo(nombre_equipo):
    """OPS de temporada del equipo. Es la referencia contra la que se compara
    el lineup del dia."""
    if nombre_equipo in _cache_ops_equipo:
        return _cache_ops_equipo[nombre_equipo]
    tid = _team_id(nombre_equipo)
    ops = None
    if tid is not None:
        try:
            d = statsapi.get("team_stats", {"teamId": tid, "stats": "season",
                                            "group": "hitting", "season": TEMPORADA,
                                            "gameType": "R"})
            ops = _float_seguro(d["stats"][0]["splits"][0]["stat"].get("ops"))
        except Exception:
            pass
    _cache_ops_equipo[nombre_equipo] = ops
    return ops


def factor_lineup(nombre_equipo, bateadores):
    """Multiplicador por la calidad del lineup publicado hoy vs el tipico del
    equipo. 1.0 si no hay alineacion o faltan datos."""
    if not bateadores:
        return 1.0
    ref = ops_equipo(nombre_equipo)
    if not ref or ref <= 0:
        return 1.0
    suma_ops, suma_pa = 0.0, 0.0
    for b in bateadores:
        ops = _float_seguro(b.get("ops_season"))
        if ops is None or ops <= 0:
            continue
        # el primer bate ve mas turnos que el noveno: pesan distinto
        pa = PA_POR_ORDEN.get(b.get("orden"), 4.2)
        suma_ops += ops * pa
        suma_pa += pa
    if suma_pa <= 0:
        return 1.0
    ops_hoy = suma_ops / suma_pa
    # El OPS es mas sensible que las carreras: se amortigua a la mitad para no
    # sobre-reaccionar a un dia de descanso de un titular.
    crudo = 1 + 0.5 * (ops_hoy / ref - 1)
    return max(1 - LINEUP_TOPE, min(crudo, 1 + LINEUP_TOPE))


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

# ---------------- BATEADORES: DATOS Y PREDICCION DE HITS ----------------

_cache_bateador = {}      # person_id -> dict con stats de bateo
_cache_lineup = {}         # game_id -> {away: [...], home: [...]}
_cache_reciente = {}       # person_id -> {avg_7: ..., avg_14: ...}

def _person_id(nombre):
    res = statsapi.lookup_player(nombre)
    return res[0]["id"] if res else None

def datos_bateador(nombre, pid=None):
    """Obtiene stats de bateo de un jugador por nombre. Cacheado por person_id."""
    if pid is None:
        pid = _person_id(nombre)
    if pid is None:
        return None
    if pid in _cache_bateador:
        return _cache_bateador[pid]
    try:
        data = statsapi.get("person", {"personId": pid, "hydrate": "stats(group=[hitting],type=[season])"})
        people = data.get("people", [])
        if not people:
            _cache_bateador[pid] = None
            return None
        p = people[0]
        bat_side = p.get("batSide", {}).get("code")
        stats_arr = p.get("stats", [])
        season_stats = None
        for g in stats_arr:
            if g.get("group", {}).get("displayName") == "hitting" and g.get("type", {}).get("displayName") == "season":
                splits = g.get("splits", [])
                if splits:
                    season_stats = splits[0]["stat"]
                    break
        if not season_stats:
            _cache_bateador[pid] = None
            return None
        s = season_stats
        ab = int(s.get("atBats", 0) or 0)
        avg = float(s["avg"]) if s.get("avg") else None
        obp = float(s["obp"]) if s.get("obp") else None
        slg = float(s["slg"]) if s.get("slg") else None
        ops = float(s["ops"]) if s.get("ops") else None
        babip = float(s["babip"]) if s.get("babip") else None
        k = int(s.get("strikeOuts", 0) or 0)
        bb = int(s.get("baseOnBalls", 0) or 0)
        h = int(s.get("hits", 0) or 0)
        hr = int(s.get("homeRuns", 0) or 0)
        k_pct = k / ab if ab > 0 else None
        bb_pct = bb / ab if ab > 0 else None
        batter = {
            "pid": pid, "nombre": nombre, "bat_side": bat_side,
            "avg": avg, "obp": obp, "slg": slg, "ops": ops,
            "babip": babip, "k_pct": k_pct, "bb_pct": bb_pct,
            "h": h, "hr": hr, "ab": ab,
        }
        # Stats recientes (ultimos 7 y 14 juegos via gameLog)
        try:
            log_data = statsapi.get("person", {"personId": pid, "hydrate": "stats(group=[hitting],type=[gameLog])"})
            log_people = log_data.get("people", [])
            log_splits = []
            if log_people:
                for g in log_people[0].get("stats", []):
                    if g.get("group", {}).get("displayName") == "hitting" and g.get("type", {}).get("displayName") == "gameLog":
                        log_splits = g.get("splits", [])
                        break
            if log_splits:
                for dias, campo in [(7, "avg_7d"), (14, "avg_14d")]:
                    recientes = [gs["stat"] for gs in log_splits[-dias:] if int(gs["stat"].get("atBats", 0) or 0) > 0]
                    if recientes:
                        rh = sum(int(g.get("hits", 0) or 0) for g in recientes)
                        rab = sum(int(g.get("atBats", 0) or 0) for g in recientes)
                        batter[campo] = rh / rab if rab > 0 else None
                    else:
                        batter[campo] = None
            else:
                batter["avg_7d"] = None
                batter["avg_14d"] = None
        except Exception:
            batter["avg_7d"] = None
            batter["avg_14d"] = None
        _cache_bateador[pid] = batter
        return batter
    except Exception as e:
        print(f"⚠ Error con bateador {nombre}: {e}")
        _cache_bateador[pid] = None
        return None

def alineacion_juego(game_id, team_name):
    """Extrae la alineacion probable de un juego desde el boxscore.
    Devuelve lista de dicts con {nombre, orden, posicion, bat_side} o None si no disponible.
    Los stats de temporada (avg, ops, obp, slg) vienen del seasonStats del boxscore."""
    if game_id in _cache_lineup:
        return _cache_lineup[game_id]
    resultado = {"away": [], "home": []}
    try:
        # Usar el game endpoint para obtener gameData.players (con batSide)
        game = statsapi.get("game", {"gamePk": game_id,
            "fields": "gameData,players,batSide,code,description,boxscoreName,"
                      "liveData,boxscore,teams,players,battingOrder,person,fullName,"
                      "allPositions,abbreviation,position,seasonStats,batting,avg,obp,slg,ops"})
        gdata = game.get("gameData", {})
        all_players = gdata.get("players", {})
        bx = game.get("liveData", {}).get("boxscore", {})
        teams = bx.get("teams", {})
        for lado in ("away", "home"):
            lado_data = teams.get(lado, {})
            jugadores = lado_data.get("players", {})
            ordenados = []
            for pid_str, pdata in jugadores.items():
                bo = pdata.get("battingOrder")
                if bo is not None:
                    nombre = pdata.get("person", {}).get("fullName", "")
                    pos = pdata.get("position", {}).get("abbreviation", "")
                    ss = pdata.get("seasonStats", {}).get("batting", {})
                    # batSide desde gameData.players
                    pid_key = "ID" + str(int(pid_str.replace("ID", "")))
                    full_pdata = all_players.get(pid_key, {})
                    bs = full_pdata.get("batSide", {}).get("code")
                    ordenados.append({
                        "pid": int(pid_str.replace("ID", "")),
                        "nombre": nombre,
                        "orden": int(str(bo)[0]) if str(bo) else 99,
                        "posicion": pos,
                        "bat_side": bs,
                        "avg_season": ss.get("avg"),
                        "obp_season": ss.get("obp"),
                        "slg_season": ss.get("slg"),
                        "ops_season": ss.get("ops"),
                    })
            # Tomar solo el primer bateador por orden (el titular)
            seen_orden = set()
            unicos = []
            for b in sorted(ordenados, key=lambda x: x["orden"]):
                if b["orden"] not in seen_orden:
                    seen_orden.add(b["orden"])
                    unicos.append(b)
            resultado[lado] = unicos[:9]
    except Exception as e:
        print(f"⚠ No se pudo obtener alineacion para juego {game_id}: {e}")
        resultado = None
    _cache_lineup[game_id] = resultado
    return resultado

def predecir_hits(avg_season, pitcher_baa, split_team, park_factor, avg_7d=None, babip=None, orden=None):
    """Calcula prediccion de hits para un bateador vs un pitcher especifico.
    Devuelve dict con avg_esperado, hits_esperados, p_hit_0, p_hit_1, p_hit_2."""
    if avg_season is None:
        return None
    avg_season = float(avg_season)
    # AVG ajustado por split de equipo vs la mano del pitcher
    avg_split = avg_season * split_team if split_team else avg_season
    avg_split = max(0.100, min(avg_split, 0.400))
    # Pitcher: su BAA o liga
    baa = pitcher_baa if pitcher_baa is not None else LIGA_AVG
    factor_pitcher = LIGA_AVG / baa if baa > 0.050 else 1.0
    factor_pitcher = max(0.70, min(factor_pitcher, 1.30))
    # Blend: season split + reciente + pitcher + park + liga
    pesos = [0.35, 0.20, 0.20, 0.15, 0.10]
    componentes = [
        avg_split,
        avg_7d if avg_7d else avg_season,
        LIGA_AVG * factor_pitcher,
        LIGA_AVG * (park_factor + 0.5) / 1.5,
        LIGA_AVG,
    ]
    total_peso = sum(pesos)
    avg_pred = sum(c * p for c, p in zip(componentes, pesos)) / total_peso
    avg_pred = max(0.080, min(avg_pred, 0.400))
    # Ajuste por BABIP: si BABIP esta muy alto/bajo, regresion hacia la media
    if babip is not None:
        desvio = float(babip) - LIGA_BABIP
        avg_pred -= desvio * 0.15  # regresion parcial
        avg_pred = max(0.080, min(avg_pred, 0.400))
    PA = PA_POR_ORDEN.get(orden, 4.2) if orden else 4.2
    p_hit_0 = (1 - avg_pred) ** PA
    p_hit_1 = PA * avg_pred * (1 - avg_pred) ** (PA - 1)
    p_hit_2 = 1 - p_hit_0 - p_hit_1
    return {
        "avg_pred": round(avg_pred, 3),
        "hits_esp": round(PA * avg_pred, 2),
        "p_hit_0": round(p_hit_0, 3),
        "p_hit_1": round(p_hit_1, 3),
        "p_hit_2": round(p_hit_2, 3),
    }

# --- cache y calculo de F5 desde datos reales ---
_cache_linescore = {}  # game_id -> linescore

def _frac_f5_de_juego(linescore):
    """Fraccion de carreras del juego que cayeron en las primeras 5 entradas.

    Solo juegos de EXACTAMENTE 9 entradas: los de extra innings meten carreras
    en el denominador que no existen en un juego de 9, y desinflaban la
    fraccion. La validacion detecto el sintoma: los totales de F5 salian 0.32
    carreras por debajo de lo real de forma consistente."""
    inn = linescore.get("innings", [])
    if len(inn) != 9:
        return None
    f5 = sum(
        (inn[i].get("home", {}).get("runs", 0) or 0)
        + (inn[i].get("away", {}).get("runs", 0) or 0)
        for i in range(5)
    )
    total = sum(
        (i.get("home", {}).get("runs", 0) or 0)
        + (i.get("away", {}).get("runs", 0) or 0)
        for i in inn
    )
    return f5 / total if total > 0 else None


F5_CACHE_FILE = "f5_frac_cache.json"

def f5_frac_liga(hoy=None):
    """Fraccion REAL de carreras en las primeras 5 entradas, calculada
    desde los juegos terminados de la temporada. Cacheada a disco."""
    global _cache_f5_frac
    if _cache_f5_frac is not None:
        return _cache_f5_frac
    import json
    if os.path.exists(F5_CACHE_FILE):
        try:
            with open(F5_CACHE_FILE) as _f:
                _cache_f5_frac = json.load(_f)
            return _cache_f5_frac
        except Exception:
            pass

    hoy = hoy or date.today().strftime("%m/%d/%Y")
    try:
        todos = statsapi.schedule(start_date=INICIO_TEMP, end_date=hoy)
    except Exception:
        _cache_f5_frac = 0.62
        return 0.62

    finales = [j for j in todos if j["status"] == "Final"]
    suma_frac = 0.0
    count = 0

    for j in finales:
        if count >= 40:
            break
        gid = j["game_id"]
        if gid not in _cache_linescore:
            try:
                _cache_linescore[gid] = statsapi.get("game_linescore", {"gamePk": gid})
            except Exception:
                continue
        frac = _frac_f5_de_juego(_cache_linescore[gid])
        if frac is not None:
            suma_frac += frac
            count += 1

    _cache_f5_frac = (suma_frac / count) if count > 0 else 0.62
    _cache_f5_frac = max(0.50, min(_cache_f5_frac, 0.75))
    try:
        with open(F5_CACHE_FILE, "w") as _f:
            json.dump(_cache_f5_frac, _f)
    except Exception:
        pass
    return _cache_f5_frac


# ---------------- VUELTAS AL ORDEN (times through the order) ----------------
# Un abridor empeora cada vez que enfrenta al lineup: los bateadores lo van
# viendo. Entre la 1a y la 3a vuelta hay ~0.6 carreras/9 de diferencia, y es de
# los efectos mas documentados del beisbol moderno (por eso los managers sacan
# al abridor en la 6a aunque vaya bien).
#
# Importa para F5 vs juego completo: en 5 entradas el abridor apenas entra a su
# 3a vuelta, mientras que en el juego completo esas entradas malas SI cuentan.
# Antes el modelo le aplicaba un FIP plano a todas sus entradas.
TTO_AJUSTE = (-0.28, 0.0, 0.32, 0.55)   # ajuste al FIP en las vueltas 1, 2, 3, 4+
INN_POR_VUELTA = 2.15                    # 9 bateadores a ~4.2 por entrada
IP_REFERENCIA = 5.5                      # duracion tipica de una apertura


def _ajuste_tto(desde, hasta):
    """Ajuste promedio por vuelta al orden en el tramo de entradas [desde, hasta)."""
    if hasta <= desde:
        return 0.0
    suma, paso, x = 0.0, 0.05, desde
    while x < hasta - 1e-9:
        tramo = min(paso, hasta - x)
        vuelta = min(int(x // INN_POR_VUELTA), len(TTO_AJUSTE) - 1)
        suma += TTO_AJUSTE[vuelta] * tramo
        x += paso
    return suma / (hasta - desde)


# El FIP de temporada YA promedia todas las vueltas de una apertura tipica. Si
# aplicaramos el ajuste crudo moveriamos el nivel global de carreras y habria
# que recalibrar AJUSTE_BASE. Se centra en la apertura de referencia para que
# el efecto sea puramente relativo (que tramo de entradas se esta evaluando).
_TTO_CENTRO = _ajuste_tto(0.0, IP_REFERENCIA)


def fip_abridor_tramo(fip, desde, hasta):
    """FIP efectivo del abridor en las entradas [desde, hasta), por vuelta al orden."""
    return fip + _ajuste_tto(desde, hasta) - _TTO_CENTRO


def fip_combinado(fip_abridor, ip_abridor, era_bullpen):
    """Pitcheo que enfrenta el rival en las 9 entradas: el abridor con su
    penalizacion por vuelta, y el bullpen cubriendo el resto."""
    fip_sp = fip_abridor_tramo(fip_abridor, 0.0, ip_abridor)
    return (fip_sp * ip_abridor + era_bullpen * (9 - ip_abridor)) / 9

def fip_f5(fip_abridor, ip_abridor, era_bullpen):
    """#F5: pitcheo que enfrenta el bateador en las primeras 5 entradas.
    El abridor cubre min(ip_esperadas, 5); si sale antes, el bullpen cubre el
    resto de las 5. Domina fuertemente el abridor, y ademas son sus MEJORES
    entradas: casi no alcanza la 3a vuelta al orden."""
    ip_en_f5 = min(ip_abridor, 5.0)
    fip_sp = fip_abridor_tramo(fip_abridor, 0.0, ip_en_f5)
    return (fip_sp * ip_en_f5 + era_bullpen * (5.0 - ip_en_f5)) / 5.0

def multiplicador_pitcheo(fip_comb):
    """ARREGLO 2: amortigua el efecto del pitcheo hacia 1.0 en vez de lineal puro."""
    crudo = fip_comb / LIGA_FIP
    return 1 + AMORTIGUA * (crudo - 1)

# NOTA: se elimino factor_kbb_comb. El FIP ya contiene K y BB en su formula
# (13*HR + 3*BB - 2*K), asi que multiplicar ademas por un factor K/BB contaba
# la misma senal dos veces y sobreconfiaba los extremos. K9/BB9 se siguen
# calculando en datos_pitcher/bullpen_stats solo como dato informativo.

def factor_defensivo(nombre_equipo):
    """Multiplicador por defensa del equipo. Basado en errores/juego.
    < 1 = buena defensa (menos carreras). > 1 = mala defensa (mas carreras)."""
    if nombre_equipo in _cache_defensa:
        return _cache_defensa[nombre_equipo]
    tid = _team_id(nombre_equipo)
    if tid is None:
        _cache_defensa[nombre_equipo] = 1.0
        return 1.0
    try:
        d = statsapi.get("team_stats", {"teamId": tid, "stats": "season",
                                        "group": "fielding", "season": TEMPORADA, "gameType": "R"})
        sp = d["stats"][0]["splits"]
        if not sp:
            _cache_defensa[nombre_equipo] = 1.0
            return 1.0
        st = sp[0]["stat"]
        errors = st.get("errors", 0) or 0
        games = st.get("games", 0) or 1
        ejuego = errors / max(games, 1)
        # Liga promedia ~0.55 errores/juego. Desviacion de ~0.15 -> ~2% de efecto
        desvio = ejuego - 0.55
        factor = 1 + desvio * 0.12
        factor = max(0.96, min(factor, 1.04))
        _cache_defensa[nombre_equipo] = factor
        return factor
    except Exception:
        _cache_defensa[nombre_equipo] = 1.0
        return 1.0

# NOTA: se elimino factor_calibracion. Comparaba el promedio ponderado por
# reciencia contra el promedio simple... pero carreras_por_juego YA es ese
# promedio ponderado (misma VIDA_MEDIA): aplicaba el mismo momentum ~1.2 veces.

def simular_binom_neg(lam, n, k=DISPERSION_K):
    """ARREGLO 1: marcadores con binomial negativa (varianza > media).
    'k' configurable: el F5 usa una dispersion mas baja que el juego completo."""
    p = k / (k + lam)
    return _rng().negative_binomial(k, p, n)

LINEAS_TT = [2.5, 3.5, 4.5, 5.5]   # totales por equipo (team totals)
N_MARCADORES = 5                    # top de marcadores mas probables
TOPE_DIST = 24                      # ultimo bin de la distribucion del total (agrupa 'N o mas').
                                    # 24 deja la cola residual en ~1% incluso en juegos de
                                    # scoring alto: asi el ultimo bin no 'rebota' en la grafica

# --- NRFI / YRFI (primera entrada) ---
FACTOR_INN1 = 1.12       # la 1ra entrada anota ~10-15% mas que la entrada promedio:
                         # siempre batea el top del lineup
DISPERSION_K_INN = 0.38  # k de la binomial negativa POR ENTRADA. Mucho mas bajo que el
                         # k del juego (4.0): la mayoria de las entradas son cero y las
                         # que anotan a veces anotan en racimo. Calibrado para reproducir
                         # ~72% de mitades sin carrera / ~52% NRFI de liga

def lambda_inning1(rg, split, mult_abridor_rival, def_rival, park, hfa=1.0):
    """Carreras esperadas de UN equipo en la 1ra entrada.
    Solo lanza el abridor rival (sin bullpen) y batea el top del orden.
    'hfa' entra ya resuelto por el lado que corresponde (HFA o 1/HFA)."""
    return rg * split * mult_abridor_rival * def_rival * park * AJUSTE_BASE * hfa / 9.0 * FACTOR_INN1

def prob_nrfi(lam1_v, lam1_c):
    """NRFI/YRFI analitico. P(0) de la binomial negativa: (k/(k+lam))^k por
    mitad de entrada; NRFI = ambas mitades en cero."""
    k = DISPERSION_K_INN
    p0_v = (k / (k + lam1_v)) ** k
    p0_c = (k / (k + lam1_c)) ** k
    nrfi = p0_v * p0_c
    return {"nrfi": nrfi, "yrfi": 1 - nrfi,
            "anota_visita": 1 - p0_v, "anota_casa": 1 - p0_c}

def simular_completo(lam_v, lam_c):
    """Simulacion completa: ademas de overs/ML/RL devuelve totales por equipo
    y los marcadores mas probables. Devuelve un dict."""
    c_v = simular_binom_neg(lam_v, N_SIMS)
    c_c = simular_binom_neg(lam_c, N_SIMS)
    tot = c_v + c_c
    empates = c_c == c_v
    moneda = _rng().random(N_SIMS) < (lam_c / (lam_c + lam_v))
    gana_c = (c_c > c_v) | (empates & moneda)
    overs = {ln: (tot > ln).mean() for ln in LINEAS}

    # totales por equipo: P(carreras del equipo > linea)
    tt_v = {ln: (c_v > ln).mean() for ln in LINEAS_TT}
    tt_c = {ln: (c_c > ln).mean() for ln in LINEAS_TT}

    # marcadores mas probables (codifica el par casa-visita en un entero)
    codigo = c_c * 1000 + c_v
    vals, counts = np.unique(codigo, return_counts=True)
    top = np.argsort(-counts)[:N_MARCADORES]
    marcadores = [{"casa": int(vals[i] // 1000), "visita": int(vals[i] % 1000),
                   "p": counts[i] / N_SIMS} for i in top]

    # Distribucion del total: es LA salida del Monte Carlo y sin ella la
    # interfaz solo ve probabilidades puntuales, perdiendo la forma (asimetria
    # y cola derecha) que justifica usar binomial negativa en vez de Poisson.
    tope = TOPE_DIST
    recortado = np.minimum(tot, tope)
    dist = np.bincount(recortado, minlength=tope + 1) / N_SIMS

    return {
        "overs": overs,
        "p_casa": gana_c.mean(),
        "p_casa_rl": ((c_c - c_v) >= 2).mean(),
        "tt_visita": tt_v,
        "tt_casa": tt_c,
        "marcadores": marcadores,
        "dist_total": dist,          # dist_total[k] = P(total == k); el ultimo bin es "k o mas"
    }

def simular(lam_v, lam_c):
    """Interfaz clasica (overs, p_casa, p_casa_rl); wrapper de simular_completo."""
    r = simular_completo(lam_v, lam_c)
    return r["overs"], r["p_casa"], r["p_casa_rl"]

def simular_f5(lam_v, lam_c):
    """#F5: tras 5 entradas el EMPATE si es un resultado real (no hay desempate).
    Devuelve (overs_f5, p_casa, p_visita, p_empate) -> ML a 3 vias."""
    c_v = simular_binom_neg(lam_v, N_SIMS, DISPERSION_K_F5)
    c_c = simular_binom_neg(lam_c, N_SIMS, DISPERSION_K_F5)
    tot = c_v + c_c
    overs = {ln: (tot > ln).mean() for ln in LINEAS_F5}
    return overs, (c_c > c_v).mean(), (c_v > c_c).mean(), (c_c == c_v).mean()

def predecir_hits_juego(visita, casa, game_id, pitcher_v, pitcher_c, park_factor, split_v, split_c):
    """Obtiene predicciones de hits para todos los bateadores en la alineacion de un juego.
    Usa los stats del boxscore directamente (sin llamadas extra por bateador) para ser rapido.
    Opcionalmente enriquece con stats detallados si ya estan cacheados."""
    lineup = alineacion_juego(game_id, None)
    if lineup is None or (not lineup.get("away") and not lineup.get("home")):
        return None
        
    from concurrent.futures import ThreadPoolExecutor
    bateadores_a_buscar = []
    for lado in ("away", "home"):
        for b in lineup.get(lado, []):
            bateadores_a_buscar.append((b["nombre"], b.get("pid")))
            
    if bateadores_a_buscar:
        with ThreadPoolExecutor(max_workers=10) as pool:
            list(pool.map(lambda x: datos_bateador(x[0], x[1]), bateadores_a_buscar))

    resultado = {"away": [], "home": []}
    for lado, abridor, split_team in [("away", pitcher_c, split_v), ("home", pitcher_v, split_c)]:
        if abridor is None:
            continue
        baa = abridor.get("baa")
        for b in lineup.get(lado, []):
            avg_season_obj = b.get("avg_season")
            if avg_season_obj is None:
                continue
            avg_s = float(avg_season_obj) if avg_season_obj else None
            if avg_s is None:
                continue
            bat_side = b.get("bat_side")
            # Intentar enriquecer con stats detallados del cache
            avg_7d = None
            babip = None
            pid = b.get("pid")
            if pid and pid in _cache_bateador:
                bdata = _cache_bateador[pid]
                if bdata:
                    avg_7d = bdata.get("avg_7d")
                    babip = bdata.get("babip")
                    if not bat_side:
                        bat_side = bdata.get("bat_side")
            pred = predecir_hits(avg_s, baa, split_team, park_factor, avg_7d, babip, b.get("orden"))
            if pred is None:
                continue
            resultado[lado].append({
                "nombre": b["nombre"],
                "orden": b.get("orden"),
                "posicion": b.get("posicion"),
                "avg_season": avg_s,
                "obp_season": b.get("obp_season"),
                "slg_season": b.get("slg_season"),
                "ops_season": b.get("ops_season"),
                "avg_7d": avg_7d,
                "babip": babip,
                "bat_side": bat_side,
                "prediccion": pred,
            })
    return resultado

# ---------------- EVALUACION DE UN JUEGO (fuente unica de verdad) ----------------
# Antes esta tuberia estaba copiada en modelo_diario.correr(), app.py,
# generar_excel.py (x3) y analisis_comparacion.py: 6 copias de la misma formula.
# Cualquier cambio de modelo habia que replicarlo 6 veces y ya provoco un bug de
# variables cruzadas. Ahora todos los consumidores llaman aqui.

def evaluar_juego(juego, hoy, frac_f5=None, con_bateo=False):
    """Evalua un juego completo del schedule y devuelve TODO lo que el modelo sabe.

    Devuelve un dict con inputs (abridores, bullpens, ofensivas), lambdas,
    probabilidades de cada mercado (ML, RL, totales, totales por equipo,
    marcadores, F5, NRFI) y banderas de calidad de datos. None si el juego
    no trae abridores anunciados.
    """
    visita, casa = juego["away_name"], juego["home_name"]
    nombre_v, nombre_c = juego.get("away_probable_pitcher"), juego.get("home_probable_pitcher")
    if not nombre_v or not nombre_c:
        return None
    if frac_f5 is None:
        frac_f5 = f5_frac_liga(hoy)

    pv = datos_pitcher(nombre_v) or {}
    pc = datos_pitcher(nombre_c) or {}

    # Nivel de reemplazo: un abridor sin stats de la temporada (regresa de lesion,
    # debuta, lo suben de ligas menores) ya no tumba el juego entero. Se le asigna
    # un perfil de reemplazo y el juego se marca como estimado.
    est_v = pv.get("fip") is None
    est_c = pc.get("fip") is None
    pv = _perfil_reemplazo(pv) if est_v else pv
    pc = _perfil_reemplazo(pc) if est_c else pc

    fip_v, fip_c = fip_blend(pv), fip_blend(pc)
    ip_v, ip_c = pv["ip_esp"], pc["ip_esp"]
    mano_v, mano_c = pv.get("mano"), pc.get("mano")

    rg_v = carreras_por_juego(visita, hoy)
    rg_c = carreras_por_juego(casa, hoy)
    park = PARK.get(casa, 1.00)
    clima = factor_clima(casa, hoy)      # ambos equipos comparten estadio y clima
    ambiente = park * clima
    split_v = split_ofensivo(visita, mano_c)   # la ofensiva visitante vs la mano del abridor local
    split_c = split_ofensivo(casa, mano_v)
    # Lineup del dia: si ya se publico, ajusta la ofensiva por quien SI juega
    lineup = alineacion_juego(juego.get("game_id"), None) if juego.get("game_id") else None
    lu_v = factor_lineup(visita, (lineup or {}).get("away"))
    lu_c = factor_lineup(casa, (lineup or {}).get("home"))
    hay_lineup = bool(lineup and ((lineup.get("away") or []) or (lineup.get("home") or [])))

    bp_v, bp_c = dict(bullpen_stats(visita)), dict(bullpen_stats(casa))
    def_v, def_c = factor_defensivo(visita), factor_defensivo(casa)

    # Bullpen quemado = peor bullpen hoy, aunque su FIP de temporada sea bueno
    fat_v = factor_fatiga_bullpen(visita, hoy)
    fat_c = factor_fatiga_bullpen(casa, hoy)
    bp_v["fip"] *= fat_v
    bp_c["fip"] *= fat_c

    # El pitcheo/defensa de un equipo deprime la ofensiva del OTRO
    pitcheo_c = fip_combinado(fip_c, ip_c, bp_c["fip"])
    pitcheo_v = fip_combinado(fip_v, ip_v, bp_v["fip"])
    lam_v = rg_v * split_v * lu_v * multiplicador_pitcheo(pitcheo_c) * def_c * ambiente * AJUSTE_BASE / HFA
    lam_c = rg_c * split_c * lu_c * multiplicador_pitcheo(pitcheo_v) * def_v * ambiente * AJUSTE_BASE * HFA

    sim = simular_completo(lam_v, lam_c)

    # F5: mismos factores, pero el abridor domina y la ofensiva se escala
    pitcheo_c_f5 = fip_f5(fip_c, ip_c, bp_c["fip"])
    pitcheo_v_f5 = fip_f5(fip_v, ip_v, bp_v["fip"])
    lam_v_f5 = rg_v * split_v * lu_v * frac_f5 * multiplicador_pitcheo(pitcheo_c_f5) * def_c * ambiente * AJUSTE_BASE / HFA
    lam_c_f5 = rg_c * split_c * lu_c * frac_f5 * multiplicador_pitcheo(pitcheo_v_f5) * def_v * ambiente * AJUSTE_BASE * HFA
    overs_f5, p_casa_f5, p_visita_f5, p_empate_f5 = simular_f5(lam_v_f5, lam_c_f5)

    # NRFI/YRFI: 1ra entrada, solo el abridor (el bullpen no participa)
    # En la 1a entrada el abridor esta en su MEJOR vuelta al orden
    l1_v = lambda_inning1(rg_v, split_v * lu_v,
                          multiplicador_pitcheo(fip_abridor_tramo(fip_c, 0.0, 1.0)),
                          def_c, ambiente, 1 / HFA)
    l1_c = lambda_inning1(rg_c, split_c * lu_c,
                          multiplicador_pitcheo(fip_abridor_tramo(fip_v, 0.0, 1.0)),
                          def_v, ambiente, HFA)

    r = {
        "visita": visita, "casa": casa,
        "abridor_v": nombre_v, "abridor_c": nombre_c,
        "pv": pv, "pc": pc,
        "fip_v": fip_v, "fip_c": fip_c, "ip_v": ip_v, "ip_c": ip_c,
        "mano_v": mano_v, "mano_c": mano_c,
        "bp_v": bp_v, "bp_c": bp_c,
        "fatiga_v": fat_v, "fatiga_c": fat_c,
        "rg_v": rg_v, "rg_c": rg_c, "split_v": split_v, "split_c": split_c,
        "park": park, "clima": clima, "def_v": def_v, "def_c": def_c,
        "lineup_v": lu_v, "lineup_c": lu_c, "hay_lineup": hay_lineup,
        "lam_v": lam_v, "lam_c": lam_c, "total_esp": lam_v + lam_c,
        "overs": sim["overs"], "p_casa": sim["p_casa"], "p_visita": 1 - sim["p_casa"],
        "p_casa_rl": sim["p_casa_rl"], "p_visita_rl": 1 - sim["p_casa_rl"],
        "tt_visita": sim["tt_visita"], "tt_casa": sim["tt_casa"],
        "marcadores": sim["marcadores"],
        "dist_total": sim["dist_total"],
        "f5": {
            "lam_v": lam_v_f5, "lam_c": lam_c_f5, "total_esp": lam_v_f5 + lam_c_f5,
            "overs": overs_f5, "p_casa": p_casa_f5, "p_visita": p_visita_f5,
            "p_empate": p_empate_f5,
            "rl_casa": p_casa_f5 + p_empate_f5, "rl_visita": p_visita_f5 + p_empate_f5,
        },
        "nrfi": prob_nrfi(l1_v, l1_c),
        # banderas de calidad: que tanto confiar en esta prediccion
        "estimado_v": est_v, "estimado_c": est_c, "estimado": est_v or est_c,
    }
    if con_bateo:
        r["bateo"] = predecir_hits_juego(visita, casa, juego.get("game_id"),
                                         pv, pc, park, split_v, split_c)   # usa el mismo cache
    return r


def _perfil_reemplazo(p):
    """Perfil de un abridor sin stats de temporada (regresa de lesion, debutante).
    FIP de reemplazo peor que la liga: quien no tiene historial reciente rinde
    por debajo del promedio, y suele salir temprano por limite de pitcheos."""
    base = dict(p or {})
    base.update({
        "fip": FIP_REEMPLAZO, "fip_reciente": None,
        "ip_temp": 0.0, "ip_reciente": 0.0,
        "ip_esp": IP_REEMPLAZO,
        "k9": base.get("k9") or LIGA_K9,
        "bb9": base.get("bb9") or LIGA_BB9,
        "baa": base.get("baa"),
        "mano": base.get("mano"),
    })
    return base


# ---------------- PROCESO PRINCIPAL ----------------

def correr(fecha=None):
    """Corre el modelo para una fecha (mm/dd/YYYY). Sin argumento usa hoy."""
    hoy = fecha or date.today().strftime("%m/%d/%Y")
    juegos = statsapi.schedule(date=hoy)
    modelables = [j for j in juegos if j["status"] in ("Scheduled", "Pre-Game", "Warmup")
                  and j["away_probable_pitcher"] and j["home_probable_pitcher"]]


    odds_slate = valor.obtener_odds()  # #2: {} si no hay ODDS_API_KEY
    _frac_f5 = f5_frac_liga(hoy)       # fraccion real de carreras en primeras 5 entradas

    print(f"=== MODELO DIARIO v3 — {hoy} ===")
    print(f"Calibracion: amortigua={AMORTIGUA} | dispersion_k={DISPERSION_K} | base={AJUSTE_BASE}")
    print(f"F5 fraccion: {_frac_f5:.3f} (vs 5/9={5/9:.3f})")
    print(f"Modelables: {len(modelables)} | Lineas de mercado: {'si' if odds_slate else 'no (sin ODDS_API_KEY)'}\n")

    filas_csv = []
    totales_slate = []
    jugadas_valor = []

    for j in modelables:
        r = evaluar_juego(j, hoy, _frac_f5)
        if r is None:
            continue
        visita, casa = r["visita"], r["casa"]
        f5 = r["f5"]
        overs, overs_f5, nrfi = r["overs"], f5["overs"], r["nrfi"]

        print(f"--- {visita} @ {casa} ---")
        print(f"Abridores: {r['abridor_v']} vs {r['abridor_c']}")
        if r["estimado"]:
            faltantes = [n for n, e in ((r['abridor_v'], r['estimado_v']),
                                        (r['abridor_c'], r['estimado_c'])) if e]
            print(f"⚠ Sin stats de {TEMPORADA}: {', '.join(faltantes)} — estimado a nivel de reemplazo")

        totales_slate.append(r["total_esp"])

        print(f"Inputs: {r['abridor_v']} FIP {r['fip_v']:.2f} {r['ip_v']:.1f}IP {r['mano_v'] or '?'} | "
              f"{r['abridor_c']} FIP {r['fip_c']:.2f} {r['ip_c']:.1f}IP {r['mano_c'] or '?'}")
        print(f"Bullpens: {visita} FIP {r['bp_v']['fip']:.2f} K/BB {r['bp_v']['k9']:.1f}/{r['bp_v']['bb9']:.1f} | "
              f"{casa} FIP {r['bp_c']['fip']:.2f} K/BB {r['bp_c']['k9']:.1f}/{r['bp_c']['bb9']:.1f}")
        print(f"Ofensivas: {visita} {r['rg_v']:.2f}x{r['split_v']:.2f} | "
              f"{casa} {r['rg_c']:.2f}x{r['split_c']:.2f} R/G | Park {r['park']}")
        print(f"Factores: DEF {r['def_c']:.3f}/{r['def_v']:.3f} | "
              f"CLIMA {r['clima']:.3f} | FATIGA bp {r['fatiga_v']:.3f}/{r['fatiga_c']:.3f} | "
              f"LINEUP {r['lineup_v']:.3f}/{r['lineup_c']:.3f}"
              f"{'' if r['hay_lineup'] else ' (sin alineacion publicada)'}")
        print(f"Carreras esp: {visita} {r['lam_v']:.2f} — {casa} {r['lam_c']:.2f} (total {r['total_esp']:.2f})")
        print(f"ML: {casa} {r['p_casa']:.1%} (justo {prob_a_momio(r['p_casa'])}, "
              f"casa ~{momio_mercado(r['p_casa'])}) | "
              f"{visita} {r['p_visita']:.1%} (justo {prob_a_momio(r['p_visita'])}, "
              f"casa ~{momio_mercado(r['p_visita'])})")
        print(f"RL: {casa} -1.5 {r['p_casa_rl']:.1%} | {visita} +1.5 {r['p_visita_rl']:.1%}")
        print("Overs:  " + " | ".join(f"O{ln} {p:.0%}" for ln, p in overs.items()))
        print("Unders: " + " | ".join(f"U{ln} {1-p:.0%}" for ln, p in overs.items()))

        print(f"[F5] Carreras esp: {visita} {f5['lam_v']:.2f} — {casa} {f5['lam_c']:.2f} "
              f"(total {f5['total_esp']:.2f})")
        print(f"[F5] ML 3 vias: {casa} {f5['p_casa']:.1%} ({prob_a_momio(f5['p_casa'])}) | "
              f"empate {f5['p_empate']:.1%} ({prob_a_momio(f5['p_empate'])}) | "
              f"{visita} {f5['p_visita']:.1%} ({prob_a_momio(f5['p_visita'])})")
        print(f"[F5] RL +0.5: {casa} {f5['rl_casa']:.1%} ({prob_a_momio(f5['rl_casa'])}) | "
              f"{visita} {f5['rl_visita']:.1%} ({prob_a_momio(f5['rl_visita'])})")
        print("[F5] Totales: " + " | ".join(f"O{ln} {p:.0%}" for ln, p in overs_f5.items()))
        print(f"[1ra] NRFI {nrfi['nrfi']:.1%} ({prob_a_momio(nrfi['nrfi'])}) | "
              f"YRFI {nrfi['yrfi']:.1%} ({prob_a_momio(nrfi['yrfi'])}) | "
              f"anota: {visita} {nrfi['anota_visita']:.1%}, {casa} {nrfi['anota_casa']:.1%}")

        jugadas = valor.analizar_juego(valor.buscar(odds_slate, visita, casa),
                                       visita, casa, r["p_casa"], overs)
        for jg in jugadas:
            print(valor.formato_jugada(jg))
            jugadas_valor.append((visita, casa, jg))
        print()

        filas_csv.append(",".join([
            hoy, visita, casa, r["abridor_v"], r["abridor_c"],
            f"{r['lam_v']:.2f}", f"{r['lam_c']:.2f}", f"{r['total_esp']:.2f}",
            f"{r['p_casa']:.3f}", f"{overs[7.5]:.3f}", f"{overs[8.5]:.3f}", f"{overs[9.5]:.3f}",
            f"{f5['total_esp']:.2f}", f"{f5['p_casa']:.3f}", f"{f5['p_empate']:.3f}",
            f"{f5['p_visita']:.3f}", f"{overs_f5[4.5]:.3f}",
            f"{f5['rl_casa']:.3f}", f"{f5['rl_visita']:.3f}",
            f"{nrfi['nrfi']:.3f}",
        ]))

    if totales_slate:
        print(f"📊 Total promedio del slate: {np.mean(totales_slate):.2f} (objetivo ~8.5)")
    if odds_slate:
        print(f"💰 Jugadas con valor (+EV > {valor.UMBRAL_EV:.0%}): {len(jugadas_valor)}")

    # ---------------- GUARDADO (anti-duplicados, #6) ----------------

    archivo = "predicciones.csv"
    CABECERA = ("fecha,visita,casa,abridor_v,abridor_c,lam_v,lam_c,total_esp,p_casa,p_over75,p_over85,p_over95,"
                "total_f5,p_casa_f5,p_empate_f5,p_visita_f5,p_over45_f5,rl_casa_f5,rl_visita_f5,p_nrfi\n")
    nuevo = not os.path.exists(archivo)
    existentes = set()
    if not nuevo:
        with open(archivo, encoding="utf-8") as f:
            lineas = f.readlines()
        # Al agregar columnas nuevas hay que reescribir la cabecera, si no el
        # historico viejo y el nuevo quedan desalineados y validar.py lee mal.
        if lineas and lineas[0] != CABECERA:
            lineas[0] = CABECERA
            with open(archivo, "w", encoding="utf-8") as f:
                f.writelines(lineas)
        for linea in lineas[1:]:
            partes = linea.split(",")
            if len(partes) >= 3:
                existentes.add((partes[0], partes[1], partes[2]))

    guardadas = 0
    with open(archivo, "a", encoding="utf-8") as f:
        if nuevo:
            f.write(CABECERA)
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

    # Registro interactivo de apuestas (se salta solo si no hay terminal)
    try:
        import tracker
        tracker.registrar(hoy, juegos)
    except Exception as e:
        print(f"(tracker no disponible: {e})")


if __name__ == "__main__":
    import sys as _sys
    correr(_sys.argv[1] if len(_sys.argv) > 1 else None)
