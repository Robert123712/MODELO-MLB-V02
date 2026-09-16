# ============================================================
# PRECIOS DEL MERCADO — observa la linea mientras existe
#
# La linea de cierre es perecedera: vive unos minutos y desaparece. Pedirsela a
# alguien despues del juego solo funciona si ese alguien se toma la molestia de
# archivarla gratis para siempre, y nadie nos debe eso. `cierres.py` intento esa
# ruta y devolvio cero en treinta fechas.
#
# Aqui se invierte el planteamiento: cada corrida anota el precio que ve de los
# juegos que TODAVIA NO EMPIEZAN. La "linea de cierre" deja de ser algo que se
# pide y pasa a ser algo que se calcula: la ultima observacion antes del primer
# pitcheo. Una vez escrita la fila es nuestra, y que el proveedor cambie su API
# o borre su historial deja de importar.
#
# Guardar TODAS las observaciones, y no solo la ultima, da el movimiento de
# linea de regalo: hacia donde se movio el dinero es informacion que el precio
# final ya no contiene.
#
# Se guarda la observacion cruda —con el id de ESPN y la hora de inicio— y NO se
# cruza con las predicciones aqui. El cruce es un problema aparte que se puede
# mejorar despues sin perder datos; si se decidiera al capturar, un cruce malo
# hoy se llevaria el precio para siempre. Por eso tambien se guardan las dobles
# carteleras, que `cierres.py` tenia que descartar: con hora de inicio e id
# propios, se resuelven al leer.
#
# Uso:  python precios.py [mm/dd/YYYY]
# ============================================================

import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ESPN = "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
ARCHIVO = "precios.csv"
COLUMNAS = ["fecha", "espn_id", "comienza", "capturado_en", "minutos_antes",
            "visita", "casa", "momio_visita", "momio_casa", "total",
            "run_line_casa", "casa_de_apuestas"]


def bajar(url):
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.load(r)


def _numero(crudo):
    try:
        return float(crudo)
    except (TypeError, ValueError):
        return None


def _momio(crudo):
    """Un momio americano nunca vive entre -100 y 100. Lo que caiga ahi no lo es."""
    n = _numero(crudo)
    return n if n is not None and abs(n) >= 100 else None


def _precio(odds):
    """El precio vigente de un juego por empezar.

    Antes del primer pitcheo no existen los campos `close` —ese bloque lo llena
    el proveedor cuando el juego termina—, asi que el precio plano ES el precio
    de ahora. Se lee ese, y no `close`, a proposito.
    """
    if not isinstance(odds, dict):
        return None
    casa = odds.get("homeTeamOdds") or {}
    visita = odds.get("awayTeamOdds") or {}
    return {
        "momio_casa": _momio(casa.get("moneyLine")),
        "momio_visita": _momio(visita.get("moneyLine")),
        "total": _numero(odds.get("overUnder")),
        "run_line_casa": _numero(odds.get("spread")),
        "casa_de_apuestas": (odds.get("provider") or {}).get("displayName")
        or (odds.get("provider") or {}).get("name") or "",
    }


def forma_de_los_momios(payload, limite=2):
    """Que campos trae de verdad el bloque de momios, cuando falta el moneyline.

    La primera captura real trajo total y run line pero ningun precio de ganador,
    y el moneyline es el mercado principal del modelo. Desde fuera no se
    distingue "ESPN no lo publica" de "lo publica en otra ruta que no estamos
    leyendo". Esto imprime las llaves disponibles para que una corrida lo diga,
    en vez de adivinar la estructura.
    """
    muestras = []
    for evento in payload.get("events", []):
        for competencia in evento.get("competitions", []):
            odds = next((o for o in competencia.get("odds", []) if isinstance(o, dict)), None)
            if not odds:
                continue
            precio = _precio(odds)
            if precio["momio_casa"] is not None or precio["momio_visita"] is not None:
                continue
            muestras.append({
                "llaves": sorted(odds.keys()),
                "homeTeamOdds": sorted((odds.get("homeTeamOdds") or {}).keys()),
                "awayTeamOdds": sorted((odds.get("awayTeamOdds") or {}).keys()),
            })
            if len(muestras) >= limite:
                return muestras
    return muestras


def observaciones(payload, capturado_en):
    """Una fila por juego con precio que todavia no empieza.

    Un juego ya empezado no aporta: su precio dejo de ser un pronostico. Y uno
    sin momios se omite en vez de guardarse en blanco, porque una fila vacia y
    una fila ausente dicen cosas distintas al leer.
    """
    ahora = datetime.fromisoformat(capturado_en)
    filas = []
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
            comienza = competencia.get("date") or evento.get("date")
            if not comienza:
                continue
            try:
                inicio = datetime.fromisoformat(comienza.replace("Z", "+00:00"))
            except ValueError:
                continue
            minutos = round((inicio - ahora).total_seconds() / 60)
            if minutos <= 0:
                continue
            odds = next((o for o in competencia.get("odds", []) if isinstance(o, dict)), None)
            precio = _precio(odds)
            if not precio or all(precio[c] is None for c in ("momio_casa", "momio_visita", "total")):
                continue
            filas.append({
                "espn_id": str(competencia.get("id") or evento.get("id") or ""),
                "comienza": inicio.astimezone(timezone.utc).isoformat(),
                "capturado_en": capturado_en,
                "minutos_antes": minutos,
                "visita": equipos["away"],
                "casa": equipos["home"],
                **precio,
            })
    return filas


def _leidas():
    if not os.path.exists(ARCHIVO):
        return []
    with open(ARCHIVO, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def guardar(filas):
    """Anade sin pisar. Una observacion es un hecho fechado: no se corrige."""
    previas = _leidas()
    existentes = {(p["espn_id"], p["capturado_en"]) for p in previas}
    nuevas = [f for f in filas if (f["espn_id"], f["capturado_en"]) not in existentes]
    if not nuevas:
        return 0
    temporal = ARCHIVO + ".tmp"
    with open(temporal, "w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS)
        escritor.writeheader()
        escritor.writerows(previas + nuevas)
    os.replace(temporal, ARCHIVO)
    return len(nuevas)


def capturar(fecha, descargar=bajar):
    mm, dd, yyyy = fecha.split("/")
    try:
        payload = descargar(f"{ESPN}?dates={yyyy}{mm}{dd}")
    except Exception as e:
        print(f"⚠ Sin precios para {fecha}: {e}")
        return []
    capturado = datetime.now(timezone.utc).isoformat()
    filas = [{"fecha": fecha, **fila} for fila in observaciones(payload, capturado)]
    if filas and all(f["momio_casa"] is None and f["momio_visita"] is None for f in filas):
        # Todas las filas sin precio de ganador: o ESPN no lo da, o vive en otra
        # ruta. Las llaves lo dicen sin tener que abrir el payload a mano.
        for muestra in forma_de_los_momios(payload):
            print(f"   sin moneyline · llaves={muestra['llaves']} "
                  f"home={muestra['homeTeamOdds']} away={muestra['awayTeamOdds']}", flush=True)
    return filas


def main(argv):
    fecha = argv[0] if argv else datetime.now().strftime("%m/%d/%Y")
    filas = capturar(fecha)
    nuevas = guardar(filas)
    if filas:
        cerca = min(f["minutos_antes"] for f in filas)
        print(f"✅ {nuevas} precios nuevos en {ARCHIVO} "
              f"({len(filas)} juegos por empezar, el mas proximo a {cerca} min del inicio)")
    else:
        # Sin juegos por empezar no hay nada que anotar, y eso es normal en la
        # corrida de la madrugada. Lo que NO es normal es que pase todo el dia.
        print(f"Sin precios que guardar para {fecha}: ningun juego por empezar con momios.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
