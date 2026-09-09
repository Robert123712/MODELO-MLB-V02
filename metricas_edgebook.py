# ============================================================
# METRICAS PARA EDGEBOOK — publica el rendimiento del modelo como JSON
#
# validar.py ya calcula Brier, log-loss, curva de calibracion y error del
# marcador. Edgebook no puede ejecutarlo: corre Python y vive en otro repo. Asi
# que aqui se corre y se publica el resultado, igual que se publica el snapshot.
# Edgebook solo lo lee y lo dibuja. NO recalcula nada.
#
# Las metricas van SEPARADAS POR model_version. Promediar dos versiones no
# describe a ninguna: el historico llego a mezclar cuatro juegos de parametros y
# la calibracion hecha sobre esa mezcla medía un modelo que ya no existia.
#
# Escribe docs/data/edgebook-metrics.json. Lo corre el workflow diario despues
# de generar el snapshot.
#
# Uso:  python metricas_edgebook.py
# ============================================================

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import validar

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCHEMA_VERSION = "1.0"
SPORT = "MLB"
# Filas anteriores a que existiera la columna model_version. NO son una version:
# son una mezcla de al menos cuatro juegos de parametros distintos. Su Brier se
# calcula igual, pero nunca se presenta como si describiera a un modelo.
SIN_SELLO = "sin sello"
SALIDA = "docs/data/edgebook-metrics.json"

# Debajo de esto, los numeros son ruido y la pantalla debe decirlo en vez de
# presentarlos como si significaran algo.
MINIMO_SIGNIFICATIVO = 100


def _mercado(pares, referencia):
    """Metricas de un mercado binario, o None si no hay con que calcularlas."""
    if not pares:
        return None
    aciertos = sum(1 for p, y in pares if (p >= 0.5) == (y == 1))
    return {
        "n": len(pares),
        "brier": round(validar.brier(pares), 4),
        "log_loss": round(validar.log_loss(pares), 4),
        "hit_rate": round(aciertos / len(pares), 4),
        # El volado es la referencia honesta: por debajo de esto el modelo
        # aporta algo, por encima no.
        "baseline_brier": referencia,
        "calibration": [
            {
                "from": lo,
                "to": hi,
                "n": n,
                "predicted": round(predicho, 4),
                "observed": round(observado, 4),
            }
            for (lo, hi), n, predicho, observado in validar.curva_calibracion(pares)
        ],
    }


def _error_total(errores):
    """MAE y sesgo del marcador proyectado. El sesgo dice hacia que lado falla."""
    if not errores:
        return None
    n = len(errores)
    media = sum(errores) / n
    return {
        "n": n,
        "mae": round(sum(abs(e) for e in errores) / n, 3),
        "bias": round(media, 3),
        # Sin esto, un sesgo de -0.19 parece una senal cuando puede ser ruido.
        # Se publica el error estandar para que la pantalla pueda decir si la
        # desviacion se distingue de cero.
        "std_error": round(
            (sum((e - media) ** 2 for e in errores) / n / n) ** 0.5, 4
        ),
    }


def recolectar():
    predicciones = validar.cargar_predicciones()
    fechas = sorted({p["fecha"] for p in predicciones}, key=validar._clave_fecha)
    cache = validar._cargar_cache()
    resultados = validar.preparar_resultados(fechas, cache)
    validar._guardar_cache(cache)

    # Un cubo por version del modelo. Mezclarlas seria describir un promedio de
    # modelos que nunca existio.
    cubos = defaultdict(
        lambda: {"ml": [], "over85": [], "f5": [], "nrfi": [], "totales": [],
                 "fechas": [], "sin_resultado": 0}
    )

    for p in predicciones:
        version = (p.get("model_version") or SIN_SELLO).strip()
        c = cubos[version]
        real = validar.resultado_prediccion(p, resultados.get(p["fecha"], {}))
        if not real:
            c["sin_resultado"] += 1
            continue
        c["fechas"].append(p["fecha"])
        gano_casa = 1 if real["rc"] > real["rv"] else 0

        p_casa = validar._f(p, "p_casa_calibrada")
        if p_casa is None:
            p_casa = validar._f(p, "p_casa")
        if p_casa is not None:
            c["ml"].append((p_casa, gano_casa))

        total_real = real["rv"] + real["rc"]
        p85 = validar._f(p, "p_over85")
        if p85 is not None:
            c["over85"].append((p85, 1 if total_real > 8.5 else 0))
        esperado = validar._f(p, "total_esp")
        if esperado is not None:
            c["totales"].append(esperado - total_real)

        if real["f5v"] is not None:
            pc5 = validar._f(p, "p_casa_f5")
            if pc5 is not None:
                c["f5"].append((pc5, 1 if real["f5c"] > real["f5v"] else 0))
        if real["inn1"] is not None:
            p_nrfi = validar._f(p, "p_nrfi")
            if p_nrfi is not None:
                c["nrfi"].append((p_nrfi, 1 if real["inn1"] == 0 else 0))

    versiones = []
    for version, c in cubos.items():
        calificadas = len(c["fechas"])
        versiones.append(
            {
                "model_version": version,
                # Sin sello no hay version que describir: el promedio de varios
                # modelos no caracteriza a ninguno, por muchos juegos que tenga.
                "unlabeled": version == SIN_SELLO,
                "from": min(c["fechas"], key=validar._clave_fecha) if c["fechas"] else None,
                "to": max(c["fechas"], key=validar._clave_fecha) if c["fechas"] else None,
                "graded": calificadas,
                "pending": c["sin_resultado"],
                # La pantalla no tiene que decidir el umbral por su cuenta.
                "significant": version != SIN_SELLO
                and calificadas >= MINIMO_SIGNIFICATIVO,
                "markets": {
                    "moneyline": _mercado(c["ml"], 0.25),
                    "total_over_85": _mercado(c["over85"], 0.25),
                    "first_five": _mercado(c["f5"], 0.25),
                    "nrfi": _mercado(c["nrfi"], 0.25),
                },
                "projected_total": _error_total(c["totales"]),
            }
        )

    # Las versiones con sello primero, y entre ellas la de mas juegos: la mezcla
    # sin sello es contexto historico, no el titular de la pantalla.
    versiones.sort(key=lambda v: (not v["unlabeled"], v["graded"]), reverse=True)
    return {
        "schema_version": SCHEMA_VERSION,
        "sport": SPORT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "minimum_meaningful": MINIMO_SIGNIFICATIVO,
        "versions": versiones,
    }


def main():
    datos = recolectar()
    os.makedirs("docs/data", exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)

    print(f"✅ {SALIDA}")
    for v in datos["versions"]:
        ml = v["markets"]["moneyline"]
        marca = (
            "  (mezcla de versiones, no describe a ningun modelo)"
            if v["unlabeled"]
            else "" if v["significant"] else "  (muestra insuficiente)"
        )
        brier = f"Brier {ml['brier']}" if ml else "sin moneyline calificado"
        print(f"   {v['model_version']}: {v['graded']} calificadas · {brier}{marca}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
