# ============================================================
# LINEA DE CIERRE — guarda el precio del mercado junto a cada prediccion
#
# El CLV (closing line value) compara lo que el modelo dijo contra el precio al
# que el mercado cerro, y es la unica medida seria que se puede calcular con
# pocos cientos de apuestas en vez de miles. Pero solo existe si la linea de
# cierre se guarda: pasado el juego, nadie la vuelve a publicar.
#
# La fuente es el marcador de ESPN, que trae los campos de apertura y cierre y
# no pide llave. Se consulta DESPUES del juego, asi que el cierre ya es
# definitivo: no es una foto tomada un rato antes.
#
# Escribe cierres.csv, un archivo aparte de predicciones.csv a proposito. La
# prediccion se escribe antes del juego y el cierre despues; mezclarlos en el
# mismo archivo obligaria a reescribir filas ya selladas.
#
# Uso:  python cierres.py [mm/dd/YYYY ...]
# Sin argumentos captura las fechas que tienen prediccion y todavia no tienen
# cierre, de la mas reciente hacia atras.
# ============================================================

import csv
import json
import os
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone

import valor
import validar

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ESPN = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
ARCHIVO = "cierres.csv"
COLUMNAS = ["fecha", "game_id", "visita", "casa", "momio_visita", "momio_casa",
            "run_line_casa", "total", "casa_de_apuestas", "capturado_en"]
# Cota por corrida: al principio hay meses de predicciones sin cierre y no se
# trata de barrer el archivo entero de un golpe.
MAXIMO_FECHAS = 30


def bajar(url):
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.load(r)


def _numero(valor_crudo):
    try:
        return float(valor_crudo)
    except (TypeError, ValueError):
        return None


def _momio(valor_crudo):
    """Un momio americano nunca vive entre -100 y 100. Lo que caiga ahi no lo es."""
    n = _numero(valor_crudo)
    return n if n is not None and abs(n) >= 100 else None


def _cierre(odds):
    """Del bloque de momios de ESPN, lo que ya cerro.

    Se prefieren los campos `close`; si el proveedor no los llena, se toma el
    precio plano, que a estas alturas ya no se mueve porque el juego termino.
    """
    if not isinstance(odds, dict):
        return None
    casa = odds.get("moneyline", {}).get("home", {}).get("close", {}) or {}
    visita = odds.get("moneyline", {}).get("away", {}).get("close", {}) or {}
    spread = odds.get("pointSpread", {}).get("home", {}).get("close", {}) or {}
    return {
        "momio_casa": _momio(casa.get("odds")) or _momio((odds.get("homeTeamOdds") or {}).get("moneyLine")),
        "momio_visita": _momio(visita.get("odds")) or _momio((odds.get("awayTeamOdds") or {}).get("moneyLine")),
        "run_line_casa": _numero(spread.get("line")) if spread.get("line") is not None else _numero(odds.get("spread")),
        "total": _numero(odds.get("overUnder")),
        "casa_de_apuestas": (odds.get("provider") or {}).get("displayName")
        or (odds.get("provider") or {}).get("name") or "",
    }


def leer_marcador(payload):
    """Cierres por par de equipos canonico. Una doble cartelera se descarta.

    Sin gamePk en ESPN el unico cruce es por equipos y fecha, y en una doble
    cartelera ese par apunta a dos juegos: guardar el precio equivocado es peor
    que no guardarlo.
    """
    encontrados = {}
    for evento in payload.get("events", []):
        for competencia in evento.get("competitions", []):
            equipos = {}
            for competidor in competencia.get("competitors", []):
                lado = competidor.get("homeAway")
                nombre = (competidor.get("team") or {}).get("displayName")
                if lado in ("home", "away") and nombre:
                    equipos[lado] = nombre
            if len(equipos) != 2:
                continue
            odds = next((o for o in competencia.get("odds", []) if isinstance(o, dict)), None)
            cierre = _cierre(odds)
            if not cierre or all(cierre[c] is None for c in ("momio_casa", "momio_visita", "total")):
                continue
            clave = (valor._canon(equipos["away"]), valor._canon(equipos["home"]))
            encontrados.setdefault(clave, []).append(
                {**cierre, "visita": equipos["away"], "casa": equipos["home"]})
    return {clave: filas[0] for clave, filas in encontrados.items() if len(filas) == 1}


def _leidos():
    if not os.path.exists(ARCHIVO):
        return []
    with open(ARCHIVO, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def fechas_pendientes(predicciones, guardados, hoy):
    """Fechas ya jugadas, con prediccion y sin un solo cierre guardado.

    De la mas reciente hacia atras. Una fecha que ESPN no cubre nunca se queda
    pendiente para siempre, y en orden viejo-primero acabaria comiendose la cuota
    de cada corrida mientras los juegos de ayer esperan.
    """
    con_cierre = {c["fecha"] for c in guardados}
    faltan = {p["fecha"] for p in predicciones
              if p["fecha"] not in con_cierre
              and validar._clave_fecha(p["fecha"]) < validar._clave_fecha(hoy)}
    return sorted(faltan, key=validar._clave_fecha, reverse=True)[:MAXIMO_FECHAS]


def capturar(fecha, predicciones, descargar=bajar):
    """Filas de cierre para una fecha. Una prediccion sin cruce no inventa precio."""
    mm, dd, yyyy = fecha.split("/")
    try:
        payload = descargar(f"{ESPN}?dates={yyyy}{mm}{dd}")
    except Exception as e:
        print(f"⚠ Sin cierres para {fecha}: {e}")
        return []
    mercado = leer_marcador(payload)
    # Un par de equipos repetido en el dia es doble cartelera: se omite entero.
    repetidos = {c for c, n in Counter(
        (valor._canon(p["visita"]), valor._canon(p["casa"]))
        for p in predicciones if p["fecha"] == fecha).items() if n > 1}
    capturado = datetime.now(timezone.utc).isoformat()
    filas = []
    for p in predicciones:
        if p["fecha"] != fecha:
            continue
        clave = (valor._canon(p["visita"]), valor._canon(p["casa"]))
        cierre = mercado.get(clave)
        if not cierre or clave in repetidos:
            continue
        filas.append({"fecha": fecha, "game_id": p.get("game_id", ""),
                      "visita": p["visita"], "casa": p["casa"],
                      **{c: cierre[c] for c in ("momio_visita", "momio_casa",
                                                "run_line_casa", "total", "casa_de_apuestas")},
                      "capturado_en": capturado})
    return filas


def guardar(filas):
    """Anade sin pisar: un cierre ya guardado es definitivo."""
    previas = _leidos()
    existentes = {(c["fecha"], c["game_id"]) for c in previas}
    nuevas = [f for f in filas if (f["fecha"], f["game_id"]) not in existentes]
    if not nuevas:
        return 0
    temporal = ARCHIVO + ".tmp"
    with open(temporal, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS)
        writer.writeheader()
        writer.writerows(previas + nuevas)
    os.replace(temporal, ARCHIVO)
    return len(nuevas)


def main(argv):
    predicciones = validar.cargar_predicciones()
    hoy = datetime.now().strftime("%m/%d/%Y")
    fechas = argv or fechas_pendientes(predicciones, _leidos(), hoy)
    if not fechas:
        print("Sin fechas pendientes de cierre.")
        return 0
    total = 0
    for fecha in fechas:
        filas = capturar(fecha, predicciones)
        total += guardar(filas)
        print(f"{fecha}: {len(filas)} cierres encontrados", flush=True)
    print(f"✅ {total} cierres nuevos en {ARCHIVO}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
