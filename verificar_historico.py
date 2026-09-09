# ============================================================
# VERIFICADOR DEL HISTORICO — integridad de predicciones.csv
#
# El GitHub Action escribe en este archivo todos los dias y no lo revisa nadie.
# Un historico corrupto no avisa: produce metricas de calibracion que parecen
# validas y no lo son. Este script las revienta antes de publicar.
#
# Comprueba:
#   1. El archivo parsea y todas las filas traen las mismas columnas
#   2. Estan las columnas indispensables
#   3. Fechas con formato mm/dd/YYYY
#   4. Probabilidades dentro de [0, 1]
#   5. (fecha, game_id) unico. La fecha va en la clave porque un juego
#      pospuesto conserva su gamePk al reprogramarse: sin ella, la fila del dia
#      suspendido y la del dia que si se jugo se ven como la misma.
#   6. El game_id se jugo de verdad esa fecha (necesita red). Solo aplica a
#      fechas ya cerradas: un partido de hoy todavia no esta Final.
#
# Las filas legacy sin game_id se omiten de 5 y 6: son anteriores a esa columna
# y no hay nada que comprobar en ellas.
#
# Uso:
#   python verificar_historico.py             -> reporta; sale 1 si hay fallas
#   python verificar_historico.py --reparar   -> arregla lo reparable (deja .bak)
#   python verificar_historico.py --sin-red   -> omite la comprobacion 6
# ============================================================

import csv
import os
import re
import shutil
import sys
from collections import Counter
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ARCHIVO = "predicciones.csv"
REQUERIDAS = ("fecha", "visita", "casa", "lam_v", "lam_c", "total_esp", "p_casa")
COLUMNAS_PROB = ("p_casa", "p_casa_calibrada", "p_over75", "p_over85", "p_over95",
                 "p_casa_f5", "p_empate_f5", "p_visita_f5", "p_over45_f5",
                 "rl_casa_f5", "rl_visita_f5", "p_nrfi")


def _numero(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _clave(fecha):
    """mm/dd/YYYY -> (YYYY, mm, dd), comparable lexicograficamente."""
    mm, dd, yyyy = fecha.split("/")
    return (yyyy, mm, dd)


def _gid(fila):
    return (fila.get("game_id") or "").strip()


def _jugados(fecha):
    """game_ids que de verdad se jugaron esa fecha, o None si no hubo conexion."""
    import statsapi
    try:
        return {str(g["game_id"]) for g in statsapi.schedule(date=fecha)
                if g.get("status") == "Final"}
    except Exception as e:
        print(f"  ⚠ {fecha}: sin conexion ({e})")
        return None


def revisar(filas, columnas, con_red=True):
    """Devuelve (problemas, reparables)."""
    problemas = []
    reparables = {"duplicadas": [], "id_ajeno": []}

    faltantes = [c for c in REQUERIDAS if c not in columnas]
    if faltantes:
        problemas.append(f"2) Faltan columnas indispensables: {faltantes}")
        return problemas, reparables

    # csv.DictReader mete los campos sobrantes bajo la clave None. Eso si es
    # corrupcion: la fila trae mas datos que columnas y el resto esta corrido.
    sobrantes = [i for i, f in enumerate(filas, 2) if None in f]
    if sobrantes:
        problemas.append(f"1) {len(sobrantes)} filas con campos de mas, los datos "
                         f"quedan corridos (lineas {sobrantes[:5]})")

    # Un valor None es lo contrario: la fila se corto antes de las ultimas
    # columnas. Pasa en el legacy anterior a p_nrfi y es inofensivo, porque esas
    # columnas simplemente no existian cuando se escribio.
    cortas = sum(1 for f in filas if None in f.values())
    if cortas:
        problemas.append(f"Nota: {cortas} filas legacy no traen las ultimas columnas "
                         f"(son anteriores a que existieran).")

    malas = [f["fecha"] for f in filas
             if not re.fullmatch(r"\d{2}/\d{2}/\d{4}", f.get("fecha") or "")]
    if malas:
        problemas.append(f"3) {len(malas)} fechas con formato invalido: {malas[:3]}")

    fuera = [(f["fecha"], c, f[c]) for f in filas for c in COLUMNAS_PROB
             if c in columnas and (v := _numero(f.get(c))) is not None
             and not 0.0 <= v <= 1.0]
    if fuera:
        problemas.append(f"4) {len(fuera)} probabilidades fuera de [0,1]: {fuera[:3]}")

    if "game_id" not in columnas:
        problemas.append("Nota: el historico es anterior a la columna game_id; "
                         "las comprobaciones 5 y 6 se omiten.")
        return problemas, reparables

    con_id = [f for f in filas if _gid(f)]

    claves = Counter((f["fecha"], _gid(f)) for f in con_id)
    repetidas = {k for k, n in claves.items() if n > 1}
    if repetidas:
        problemas.append(f"5) {len(repetidas)} juegos con mas de una fila: "
                         f"{list(repetidas)[:3]}")
        vistas = set()
        for f in con_id:
            clave = (f["fecha"], _gid(f))
            if clave in repetidas:
                if clave in vistas:
                    reparables["duplicadas"].append(f)
                vistas.add(clave)

    if con_red:
        # Solo fechas ya cerradas. Un partido de hoy todavia no esta Final, y
        # tratarlo como "no se jugo esa fecha" haria que --reparar le borrara el
        # game_id a la prediccion recien capturada.
        hoy = datetime.now().strftime("%m/%d/%Y")
        cerradas = [f for f in con_id if _clave(f["fecha"]) < _clave(hoy)]
        pendientes = len(con_id) - len(cerradas)
        if pendientes:
            print(f"· {pendientes} filas de hoy o posteriores: su resultado aun no existe, "
                  f"se revisaran cuando la fecha cierre.")

        fechas = sorted({f["fecha"] for f in cerradas}, key=_clave)
        ajenas = []
        for i, fecha in enumerate(fechas, 1):
            print(f"  [{i}/{len(fechas)}] {fecha}", end="\r", flush=True)
            reales = _jugados(fecha)
            if reales is None:
                continue
            ajenas += [f for f in cerradas if f["fecha"] == fecha and _gid(f) not in reales]
        print(" " * 40, end="\r")
        if ajenas:
            problemas.append(
                f"6) {len(ajenas)} filas con un game_id que no se jugo esa fecha. "
                f"Son juegos pospuestos: el gamePk apunta al de reposicion.")
            reparables["id_ajeno"] = ajenas

    return problemas, reparables


def reparar(filas, reparables):
    """Quita filas repetidas y limpia los game_id que no corresponden.

    Un game_id ajeno se BORRA en vez de corregirse: la prediccion se hizo para un
    juego que ese dia no se jugo, asi que no existe resultado contra el cual
    calificarla. Sin id queda fuera de las metricas, que es lo correcto.
    """
    sobran = {id(f) for f in reparables["duplicadas"]}
    limpias = [f for f in filas if id(f) not in sobran]
    limpiadas = 0
    for f in reparables["id_ajeno"]:
        if id(f) not in sobran:
            f["game_id"] = ""
            limpiadas += 1
    return limpias, len(sobran), limpiadas


def main(aplicar=False, con_red=True):
    if not os.path.exists(ARCHIVO):
        print(f"No existe {ARCHIVO}.")
        return 1

    with open(ARCHIVO, encoding="utf-8", newline="") as f:
        lector = csv.DictReader(f)
        columnas = list(lector.fieldnames or [])
        filas = list(lector)

    print(f"{len(filas)} filas, {len(columnas)} columnas en {ARCHIVO}")
    if con_red:
        print("Comprobando game_id contra el schedule real (usa red)...")

    problemas, reparables = revisar(filas, columnas, con_red)
    fallas = [p for p in problemas if not p.startswith("Nota:")]

    print()
    for p in problemas:
        print(("❌ " if not p.startswith("Nota:") else "· ") + p)
    if not fallas:
        print("✅ Sin problemas.")
        return 0

    reparable = len(reparables["duplicadas"]) + len(reparables["id_ajeno"])
    if not reparable:
        print("\nNinguno se puede reparar automaticamente.")
        return 1
    if not aplicar:
        print(f"\n{reparable} filas son reparables. Corre con --reparar.")
        return 1

    limpias, quitadas, limpiadas = reparar(filas, reparables)
    shutil.copy2(ARCHIVO, ARCHIVO + ".bak")
    temporal = ARCHIVO + ".tmp"
    with open(temporal, "w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=columnas, extrasaction="ignore")
        escritor.writeheader()
        escritor.writerows(limpias)
    os.replace(temporal, ARCHIVO)

    print(f"\n✅ {quitadas} filas duplicadas eliminadas, {limpiadas} game_id ajenos "
          f"limpiados. Respaldo en {ARCHIVO}.bak")
    return 0


if __name__ == "__main__":
    sys.exit(main(aplicar="--reparar" in sys.argv,
                  con_red="--sin-red" not in sys.argv))
