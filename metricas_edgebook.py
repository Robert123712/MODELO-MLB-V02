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

import mercado
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

# Las lineas que el modelo publica y el historico registra. Cada una se califica
# como su propio mercado: su muestra es el numero de juegos, no el numero de
# llamadas.
LINEAS_TOTAL = [5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5]
LINEAS_TT = [2.5, 3.5, 4.5, 5.5]
# Totales de las primeras cinco entradas. Se publicaban en pantalla sin
# calificarse: el unico mercado F5 medido era quien iba ganando. Mostrar una
# probabilidad que nunca se contrasta es exactamente lo que este archivo existe
# para evitar.
LINEAS_F5 = [2.5, 3.5, 4.5, 5.5, 6.5]
# Spread de F5, con el margen minimo que cubre cada linea. Se escriben los
# umbrales igual que en el juego completo: dando 0.5 hay que ir arriba (margen
# 1), recibiendo 0.5 basta con no ir abajo (margen 0, el empate cubre).
RUN_LINE_F5 = [("m05", 1), ("m15", 2), ("p05", 0), ("p15", -1)]
# La casa dando (m) o recibiendo (p) carreras, con el margen minimo que cubre.
# Se escriben los umbrales en vez de derivarlos del signo: dando 2.5 hay que
# ganar por 3, recibiendo 2.5 basta con no perder por mas de 2, y equivocar ese
# signo califica el mercado contrario sin que nada falle a gritos.
# La visita es el complemento exacto, asi que publicarla seria contar dos veces
# el mismo evento.
RUN_LINE = [("m15", 2), ("m25", 3), ("p15", -1), ("p25", -2)]


def _sufijo(linea):
    """5.5 -> '55'. Es como se nombran las columnas del historico."""
    return str(linea).replace(".", "")


def _mercado(pares, referencia):
    """Metricas de un mercado binario, o None si no hay con que calcularlas."""
    if not pares:
        return None
    aciertos = sum(1 for p, y in pares if (p >= 0.5) == (y == 1))
    ocurrio = sum(y for _, y in pares) / len(pares)
    return {
        "n": len(pares),
        # Con que frecuencia paso el evento. De aqui sale la regla fija: decir
        # siempre que si acierta esto, decir siempre que no acierta lo contrario.
        # Sin esa referencia, un acierto alto puede ser solo el equipo local
        # ganando seguido, y no el modelo eligiendo bien.
        "base_rate": round(ocurrio, 4),
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


def _resumen_error(errores):
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


def _resumen_captura(minutos):
    """Que tan pegada al primer pitcheo quedo la observacion que se llamo cierre.

    Con cinco corridas al dia el hueco varia por juego. Publicarlo deja que la
    pantalla diga la verdad —"la ultima lectura fue a 40 minutos"— en vez de
    presentar cualquier foto del dia como si fuera la linea final.
    """
    if not minutos:
        return None
    ordenados = sorted(minutos)
    return {"n": len(ordenados),
            "mediana_minutos": round(ordenados[len(ordenados) // 2], 1),
            "peor_minutos": round(ordenados[-1], 1)}


def _error_total(fechados):
    """MAE y sesgo del marcador proyectado, con su desglose por mes.

    El total acumulado dice que el modelo proyecta corto, pero no si eso es de
    siempre o del mes que corre: septiembre no se anota como abril. Sin el
    desglose, mover la calibracion seria perseguir al ultimo mes.
    """
    if not fechados:
        return None
    por_mes = defaultdict(list)
    for fecha, error in fechados:
        mes, _, anio = fecha.split("/")
        por_mes[f"{anio}-{mes}"].append(error)
    return {
        **_resumen_error([error for _, error in fechados]),
        "by_month": [{"month": mes, **_resumen_error(errores)}
                     for mes, errores in sorted(por_mes.items())],
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
        lambda: {"mercados": defaultdict(list), "totales": [],
                 "cierre_total": [], "minutos_cierre": [],
                 "fechas": [], "sin_resultado": 0}
    )

    # La linea de cierre entra SOLO para comparar: nunca alimenta al modelo.
    # Si el modelo no le gana, no aporta nada que no estuviera ya en el precio.
    precios = mercado.indexar(mercado.cargar())

    for p in predicciones:
        version = (p.get("model_version") or SIN_SELLO).strip()
        c = cubos[version]
        real = validar.resultado_prediccion(p, resultados.get(p["fecha"], {}))
        if not real:
            c["sin_resultado"] += 1
            continue
        c["fechas"].append(p["fecha"])
        gano_casa = 1 if real["rc"] > real["rv"] else 0

        mercados = c["mercados"]

        def anotar(nombre, columna, ocurrio):
            """Una llamada del modelo contra lo que paso. Sin columna, no hubo llamada."""
            probabilidad = validar._f(p, columna)
            if probabilidad is not None:
                mercados[nombre].append((probabilidad, 1 if ocurrio else 0))

        p_casa = validar._f(p, "p_casa_calibrada")
        if p_casa is None:
            p_casa = validar._f(p, "p_casa")
        if p_casa is not None:
            mercados["moneyline"].append((p_casa, gano_casa))

        total_real = real["rv"] + real["rc"]
        # Cada linea se califica por separado y su muestra es el numero de
        # juegos. Juntarlas multiplicaria la n sin agregar informacion: las ocho
        # salen de la misma simulacion y se mueven juntas.
        for linea in LINEAS_TOTAL:
            anotar(f"total_over_{_sufijo(linea)}", f"p_over{_sufijo(linea)}",
                   total_real > linea)
        margen = real["rc"] - real["rv"]
        for columna, minimo in RUN_LINE:
            anotar(f"run_line_casa_{columna}", f"rl_casa_{columna}", margen >= minimo)
        for lado, carreras in (("visita", real["rv"]), ("casa", real["rc"])):
            for linea in LINEAS_TT:
                anotar(f"team_total_{lado}_{_sufijo(linea)}",
                       f"tt_{lado}_{_sufijo(linea)}", carreras > linea)

        esperado = validar._f(p, "total_esp")
        if esperado is not None:
            c["totales"].append((p["fecha"], esperado - total_real))

        cerro = mercado.cierre(precios, p["visita"], p["casa"], p.get("game_datetime"))
        if cerro:
            del_mercado = mercado.sin_vig(cerro.get("momio_visita"), cerro.get("momio_casa"))
            if del_mercado is not None:
                mercados["closing_moneyline"].append((del_mercado, gano_casa))
            total_cierre = mercado._numero(cerro.get("total"))
            if total_cierre is not None:
                c["cierre_total"].append((p["fecha"], total_cierre - total_real))
            minutos = mercado._numero(cerro.get("minutos_antes"))
            if minutos is not None:
                # Cuanto se parecia al cierre de verdad. Sin esto, una foto de
                # hace seis horas se presentaria como si fuera la linea final.
                c["minutos_cierre"].append(minutos)

        if real["f5v"] is not None:
            anotar("first_five", "p_casa_f5", real["f5c"] > real["f5v"])
            total_f5 = real["f5v"] + real["f5c"]
            for linea in LINEAS_F5:
                anotar(f"total_f5_over_{_sufijo(linea)}",
                       f"p_over{_sufijo(linea)}_f5", total_f5 > linea)
            margen_f5 = real["f5c"] - real["f5v"]
            for columna, minimo in RUN_LINE_F5:
                anotar(f"run_line_f5_casa_{columna}", f"rl_casa_f5_{columna}",
                       margen_f5 >= minimo)
        if real["inn1"] is not None:
            anotar("nrfi", "p_nrfi", real["inn1"] == 0)

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
                "markets": {nombre: _mercado(pares, 0.25)
                            for nombre, pares in c["mercados"].items() if pares},
                "projected_total": _error_total(c["totales"]),
                # Mismo formato que publica NFL, para que la pantalla lea igual
                # los dos deportes. `margin` no aplica en beisbol.
                "closing_line": {"total": _error_total(c["cierre_total"]), "margin": None},
                "closing_capture": _resumen_captura(c["minutos_cierre"]),
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
        # Un mercado sin una sola llamada calificada no aparece en el dict: la
        # ausencia dice "nunca se midio", que no es lo mismo que un cero.
        ml = v["markets"].get("moneyline")
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
