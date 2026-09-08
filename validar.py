# ============================================================
# VALIDADOR DE CALIBRACION
# Cruza el historico de predicciones.csv contra los resultados reales de MLB
# y responde la unica pregunta que importa: ¿le puedo creer al modelo?
#
#   Brier score   -> error cuadratico medio de las probabilidades (mas bajo mejor)
#   Log-loss      -> castiga fuerte la confianza equivocada (mas bajo mejor)
#   Calibracion   -> cuando el modelo dice 60%, ¿pega 60% de las veces?
#   MAE / bias    -> que tan lejos quedan los totales, y hacia que lado
#
# Uso:
#   python validar.py                -> valida todo el historico
#   python validar.py 07/01/2026     -> solo desde esa fecha en adelante
#
# Los resultados reales se cachean en resultados_cache.json para no volver a
# pegarle a la API en cada corrida.
# ============================================================

import csv
import json
import math
import os
import sys
from collections import defaultdict

import statsapi

from modelo_diario import calibrar_ml   # capa Platt del ML (identidad si esta apagada)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ARCHIVO_PRED = "predicciones.csv"
CACHE_RESULTADOS = "resultados_cache.json"

# Los tramos de la curva de calibracion. Bordes en 0.5 para separar
# favoritos de no favoritos.
TRAMOS = [(0.0, 0.35), (0.35, 0.45), (0.45, 0.50),
          (0.50, 0.55), (0.55, 0.65), (0.65, 1.01)]


# ---------------- RESULTADOS REALES (con cache a disco) ----------------

def _cargar_cache():
    if os.path.exists(CACHE_RESULTADOS):
        try:
            with open(CACHE_RESULTADOS, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _guardar_cache(cache):
    try:
        with open(CACHE_RESULTADOS, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception as e:
        print(f"⚠ No se pudo guardar el cache: {e}")


def resultados_de_fecha(fecha, cache):
    """Resultados por gamePk y por nombres SOLO cuando el cruce es unico.
    Cachea exclusivamente jornadas completas; un vacio no congela pendientes.
    """
    from collections import Counter
    entrada = cache.get(fecha, {})
    if entrada.get("schema_version") == 2 and entrada.get("completa"):
        return {tuple(k.split("|")): v for k, v in entrada["juegos"].items()}
    try:
        data = statsapi.get("schedule", {"sportId": 1, "date": fecha, "hydrate": "linescore"}, request_kwargs={"timeout": 20})
    except Exception as e:
        print(f"Sin conexion para {fecha}: {e}")
        return {}
    juegos = [g for d in data.get("dates", []) for g in d.get("games", [])]
    def clave(g):
        return tuple(g["teams"][lado]["team"]["name"] for lado in ("away", "home"))
    cuentas = Counter(clave(g) for g in juegos)
    out = {}
    completa = bool(juegos)
    for g in juegos:
        if g["status"].get("detailedState") != "Final":
            completa = False
            continue
        t = g["teams"]
        if any(t[lado].get("score") is None for lado in ("away", "home")):
            completa = False
            continue
        dato = {"rv": t["away"]["score"], "rc": t["home"]["score"],
                "f5v": None, "f5c": None, "inn1": None, "game_id": str(g["gamePk"])}
        innings = g.get("linescore", {}).get("innings", [])
        def completas(n):
            return len(innings) >= n and all(
                i.get(lado, {}).get("runs") is not None
                for i in innings[:n] for lado in ("away", "home"))
        if completas(5):
            dato["f5v"] = sum(i["away"]["runs"] for i in innings[:5])
            dato["f5c"] = sum(i["home"]["runs"] for i in innings[:5])
        else:
            completa = False
        if completas(1):
            dato["inn1"] = innings[0]["away"]["runs"] + innings[0]["home"]["runs"]
        out[(str(g["gamePk"]),)] = dato
        if cuentas[clave(g)] == 1:
            out[clave(g)] = dato
    cache[fecha] = {"schema_version": 2, "completa": completa,
                    "juegos": {"|".join(k): v for k, v in out.items()}}
    return out


def preparar_resultados(fechas, cache):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    resultado = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        pendientes = {pool.submit(resultados_de_fecha, fecha, cache): fecha for fecha in fechas}
        for future in as_completed(pendientes):
            fecha = pendientes[future]
            resultado[fecha] = future.result()
            print(f"Resultados {len(resultado)}/{len(fechas)}: {fecha}", flush=True)
    _guardar_cache(cache)
    return resultado


def resultado_prediccion(p, reales):
    key = (p["game_id"],) if p.get("game_id") else (p["visita"], p["casa"])
    return reales.get(key)


def cargar_predicciones():
    """Conserva la primera prediccion por ID (legacy: fecha/equipos)."""
    with open(ARCHIVO_PRED, encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    vistos, salida = set(), []
    for p in filas:
        if p.get("generado_en") or p.get("game_datetime"):
            from datetime import datetime
            try:
                emitido = datetime.fromisoformat(p["generado_en"].replace("Z", "+00:00"))
                inicio = datetime.fromisoformat(p["game_datetime"].replace("Z", "+00:00"))
                if emitido.tzinfo is None or inicio.tzinfo is None or emitido >= inicio:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
        key = (p["fecha"], p.get("game_id") or (p["visita"], p["casa"]))
        if key not in vistos:
            vistos.add(key)
            salida.append(p)
    return salida


# ---------------- METRICAS ----------------

def brier(pares):
    """Error cuadratico medio. Referencia: 0.25 = tirar un volado."""
    return sum((p - y) ** 2 for p, y in pares) / len(pares)


def log_loss(pares):
    """Castiga la confianza equivocada. Referencia: 0.693 = volado."""
    eps = 1e-9
    total = 0.0
    for p, y in pares:
        p = min(max(p, eps), 1 - eps)
        total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
    return total / len(pares)


def curva_calibracion(pares):
    """Agrupa por tramo de probabilidad: predicho vs observado."""
    grupos = defaultdict(list)
    for p, y in pares:
        for lo, hi in TRAMOS:
            if lo <= p < hi:
                grupos[(lo, hi)].append((p, y))
                break
    filas = []
    for tramo in TRAMOS:
        g = grupos.get(tramo)
        if not g:
            continue
        predicho = sum(p for p, _ in g) / len(g)
        observado = sum(y for _, y in g) / len(g)
        filas.append((tramo, len(g), predicho, observado))
    return filas


def _reporte_mercado(nombre, pares, referencia=None):
    if not pares:
        return
    b, ll = brier(pares), log_loss(pares)
    aciertos = sum(1 for p, y in pares if (p > 0.5) == (y == 1))
    print(f"\n{nombre}  (n = {len(pares)})")
    print(f"  Brier score:   {b:.4f}   (volado = 0.2500{'' if referencia is None else f', mercado tipico ~{referencia}'})")
    print(f"  Log-loss:      {ll:.4f}   (volado = 0.6931)")
    print(f"  Acierto:       {aciertos}/{len(pares)} ({aciertos / len(pares):.1%})")
    print(f"  {'Tramo':>12s} {'n':>5s} {'predicho':>9s} {'observado':>10s} {'sesgo':>7s}")
    for (lo, hi), n, pred, obs in curva_calibracion(pares):
        marca = ""
        if n >= 15:
            dif = pred - obs
            if abs(dif) > 0.10:
                marca = "  <- descalibrado"
            elif abs(dif) > 0.05:
                marca = "  <- revisar"
        print(f"  {lo:.2f}-{hi:.2f}".rjust(14) + f" {n:5d} {pred:9.1%} {obs:10.1%} "
              f"{pred - obs:+7.1%}{marca}")


# ---------------- PROCESO ----------------

def _f(fila, campo):
    try:
        valor = float(fila[campo])
        return valor if math.isfinite(valor) else None
    except (TypeError, ValueError, KeyError):
        return None


def validar(desde=None):
    if not os.path.exists(ARCHIVO_PRED):
        print(f"No existe {ARCHIVO_PRED}. Corre el modelo primero.")
        return 1

    predicciones = cargar_predicciones()
    if desde:
        predicciones = [p for p in predicciones if _clave_fecha(p["fecha"]) >= _clave_fecha(desde)]
    if not predicciones:
        print("No hay predicciones en ese rango.")
        return 1

    cache = _cargar_cache()
    ml, ml_crudo, over85, f5_casa, nrfi = [], [], [], [], []
    err_totales, err_f5 = [], []
    sin_resultado = 0
    fechas = sorted({p["fecha"] for p in predicciones}, key=_clave_fecha)

    print(f"Validando {len(predicciones)} predicciones en {len(fechas)} fechas "
          f"({fechas[0]} → {fechas[-1]})...")

    resultados = preparar_resultados(fechas, cache)
    for fecha in fechas:
        reales = resultados[fecha]
        for p in (x for x in predicciones if x["fecha"] == fecha):
            real = resultado_prediccion(p, reales)
            if not real:
                sin_resultado += 1
                continue

            gano_casa = 1 if real["rc"] > real["rv"] else 0
            total_real = real["rv"] + real["rc"]

            p_casa = _f(p, "p_casa")   # el CSV guarda la prob CRUDA del ML
            if p_casa is not None:
                ml_crudo.append((p_casa, gano_casa))
                ml.append((_f(p, "p_casa_calibrada") if _f(p, "p_casa_calibrada") is not None else calibrar_ml(p_casa), gano_casa))   # como sale ya calibrado
            p_o85 = _f(p, "p_over85")
            if p_o85 is not None and total_real != 8.5:
                over85.append((p_o85, 1 if total_real > 8.5 else 0))
            total_esp = _f(p, "total_esp")
            if total_esp is not None:
                err_totales.append(total_esp - total_real)

            # F5 y NRFI solo si hubo marcador por entrada
            if real["f5v"] is not None:
                pc5 = _f(p, "p_casa_f5")
                if pc5 is not None:
                    f5_casa.append((pc5, 1 if real["f5c"] > real["f5v"] else 0))
                tf5 = _f(p, "total_f5")
                if tf5 is not None:
                    err_f5.append(tf5 - (real["f5v"] + real["f5c"]))
            if real["inn1"] is not None:
                p_nrfi = _f(p, "p_nrfi")
                if p_nrfi is not None:
                    nrfi.append((p_nrfi, 1 if real["inn1"] == 0 else 0))

    _guardar_cache(cache)

    print("\n" + "=" * 64)
    print("           VALIDACION DE CALIBRACION DEL MODELO")
    print("=" * 64)
    print(f"Predicciones con resultado: {len(ml)} | sin resultado aun: {sin_resultado}")

    _reporte_mercado("MONEYLINE (emitido si existe; legacy recalibrado retrospectivo)", ml, referencia="0.23")
    if ml_crudo:
        print(f"  (Brier CRUDO sin calibrar: {brier(ml_crudo):.4f} -> calibrado {brier(ml):.4f})")
    _reporte_mercado("TOTAL Over 8.5", over85)
    _reporte_mercado("F5 — gana la casa", f5_casa)
    _reporte_mercado("NRFI (1ª entrada sin carreras)", nrfi)

    if err_totales:
        _reporte_error("TOTALES (juego completo)", err_totales)
    if err_f5:
        _reporte_error("TOTALES F5", err_f5)

    print("\n" + "=" * 64)
    print("COMO LEERLO")
    print("  El legacy recalibrado NO es prueba fuera de muestra. Usa calibrar.py.")
    print("  Ganar al volado no demuestra superar al mercado ni rentabilidad.")
    print("  En la curva, 'sesgo' positivo = el modelo dice mas de lo que pasa")
    print("  (sobreconfiado). Un tramo descalibrado con n grande es accionable:")
    print("  ahi el modelo se equivoca de forma sistematica, no por azar.")
    print("  Ojo: con menos de ~200 juegos por mercado todo esto es orientativo.")
    return 0


def _reporte_error(nombre, errores):
    n = len(errores)
    mae = sum(abs(e) for e in errores) / n
    bias = sum(errores) / n
    rmse = math.sqrt(sum(e * e for e in errores) / n)
    print(f"\n{nombre}  (n = {n})")
    print(f"  MAE:   {mae:.3f} carreras")
    print(f"  RMSE:  {rmse:.3f}")
    print(f"  Bias:  {bias:+.3f}  ({'sobreestima' if bias > 0 else 'subestima'} el total)")
    if abs(bias) > 0.35 and n >= 50:
        signo = "bajar" if bias > 0 else "subir"
        print(f"  <- sesgo consistente: considera {signo} AJUSTE_BASE en modelo_diario.py")


def _clave_fecha(f):
    """mm/dd/YYYY -> ordenable."""
    mm, dd, yyyy = f.split("/")
    return (int(yyyy), int(mm), int(dd))


if __name__ == "__main__":
    sys.exit(validar(sys.argv[1] if len(sys.argv) > 1 else None))
