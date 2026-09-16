# ============================================================
# GENERADOR DE JSON — snapshot diario para la pagina web estatica
# Corre la simulacion del dia (reutiliza la logica de app.py) y escribe:
#   docs/data/latest.json          <- lo que lee la pagina de GitHub Pages
#   docs/data/YYYY-MM-DD.json      <- copia con fecha (historico)
# Uso:  python generar_json.py [mm/dd/YYYY]
# Lo ejecuta el GitHub Action de corridas automaticas (modelo-diario.yml).
# ============================================================

import json
import os
import sys
from datetime import date, datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import contrato_edgebook
from app import SimularRequest, _ejecutar_simulacion


def _clave(juego):
    return (juego.get("visita"), juego.get("casa"), str(juego.get("game_datetime") or ""))


def conservar_publicados(nuevos, previos):
    """Los juegos de hoy que ya se publicaron siguen publicados.

    Un partido sale de la simulacion en cuanto empieza: el modelo pide abridores
    probables y un estado previo al primer pitcheo, y statsapi deja de dar ambos.
    El resultado era que el juego desaparecia de la pantalla justo al arrancar.

    Aqui no se vuelve a simular nada. Se conserva la MISMA proyeccion que ya se
    habia publicado antes del inicio, que ademas es la unica honesta para un
    juego en curso: es lo que el modelo dijo, no una estimacion de ahora. El
    orden lo pone la hora de inicio para que la lista no baile entre corridas.
    """
    vistos = {_clave(j) for j in nuevos}
    rescatados = [j for j in previos if _clave(j) not in vistos]
    return sorted(nuevos + rescatados, key=lambda j: str(j.get("game_datetime") or ""))


def _previos(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f).get("juegos", [])
    except (OSError, ValueError):
        # Primera corrida del dia, o archivo ilegible: no hay nada que conservar
        # y eso no es un error, es el estado normal de la manana.
        return []


def generar(fecha=None):
    hoy = fecha or date.today().strftime("%m/%d/%Y")
    print(f"📡 Simulando slate del {hoy} para la página...", flush=True)

    data = _ejecutar_simulacion(SimularRequest(fecha=hoy))
    data["generado_en"] = datetime.now(timezone.utc).isoformat()

    os.makedirs("docs/data", exist_ok=True)

    mm, dd, yyyy = hoy.split("/")
    con_fecha = f"docs/data/{yyyy}-{mm}-{dd}.json"
    antes = len(data["juegos"])
    data["juegos"] = conservar_publicados(data["juegos"], _previos(con_fecha))
    if len(data["juegos"]) > antes:
        print(f"   {len(data['juegos']) - antes} juego(s) ya empezado(s): se conserva "
              "la proyeccion publicada antes del primer pitcheo", flush=True)
    for ruta in ("docs/data/latest.json", con_fecha):
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    # Snapshot en el formato que consume Edgebook, en archivos aparte: la pagina
    # de GitHub Pages y Edgebook no deberian romperse el uno al otro.
    edgebook = contrato_edgebook.envelope(data)
    eb_con_fecha = f"docs/data/edgebook-{yyyy}-{mm}-{dd}.json"
    for ruta in ("docs/data/edgebook-latest.json", eb_con_fecha):
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(edgebook, f, ensure_ascii=False)

    print(f"✅ {len(data['juegos'])} juegos → docs/data/latest.json y {con_fecha}", flush=True)
    omitidos = edgebook["skipped_without_id"]
    print(f"✅ {len(edgebook['games'])} juegos → docs/data/edgebook-latest.json"
          + (f" ({omitidos} omitidos sin game_id)" if omitidos else ""), flush=True)


if __name__ == "__main__":
    generar(sys.argv[1] if len(sys.argv) > 1 else None)
