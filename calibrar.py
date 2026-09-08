# ============================================================
# CALIBRADOR DE PROBABILIDADES
# Ajusta la capa de calibracion del modelo sobre el historico REAL.
#
# El motor produce probabilidades CRUDAS de la simulacion Monte Carlo. La
# validacion mostro que el moneyline crudo esta mal calibrado: subvalua al
# local y se sobre-confia en los extremos. En vez de mover HFA o la dispersion
# "a ojo" (romperia los totales, que si estan bien), se ajusta una capa Platt
# sobre la probabilidad FINAL del ML:
#
#     p_cal = sigmoid(A + B * logit(p_cruda))
#
#   - A (intercepto) corrige el CENTRO: cuanto subvalua/sobrevalua al local.
#   - B (pendiente)  corrige la CONFIANZA: B<1 encoge hacia 50% (menos
#     sobreconfianza), B>1 la estira.
#
# Se ajusta por minimos de log-loss (regresion logistica de 1 variable via
# Newton-Raphson) y se comprueba con un SPLIT TEMPORAL: entrena en los juegos
# viejos, mide en los nuevos. Si el Brier de prueba baja, la capa generaliza y
# no es curva-ajuste al ruido. Los A, B que imprime se pegan como constantes en
# modelo_diario.py (ML_CAL_A / ML_CAL_B). NO se mueven a mano.
#
# Tambien calcula el AJUSTE_BASE sugerido para centrar el sesgo de totales,
# que es lineal en el nivel de carreras.
#
# Uso (necesita conexion a la API de MLB; correr en el workflow "Validar"):
#   python calibrar.py                -> usa todo el historico
#   python calibrar.py 07/01/2026     -> desde esa fecha
# ============================================================

import csv
import math
import os
import sys

import validar  # reutiliza la carga de resultados reales y el cache a disco
from modelo_diario import AJUSTE_BASE

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def ajustar_platt(pares, iteraciones=50):
    """Regresion logistica de 1 variable: y ~ sigmoid(A + B*logit(p)).
    Devuelve (A, B). Newton-Raphson con Hessiano 2x2 invertido a mano."""
    if not pares or len({y for _, y in pares}) < 2:
        return 0.0, 1.0
    zs = [_logit(p) for p, _ in pares]
    ys = [y for _, y in pares]
    a, b = 0.0, 1.0
    for _ in range(iteraciones):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for z, y in zip(zs, ys):
            mu = _sigmoid(a + b * z)
            d = mu - y
            g0 += d
            g1 += d * z
            w = mu * (1 - mu)
            h00 += w
            h01 += w * z
            h11 += w * z * z
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-12:
            break
        # theta -= H^-1 g
        da = (h11 * g0 - h01 * g1) / det
        db = (-h01 * g0 + h00 * g1) / det
        # Backtracking evita saltos de Newton que saturan o invierten el modelo.
        actual = _logloss(_aplicar(pares, a, b))
        paso = 1.0
        while paso > 1e-8:
            na, nb = a - paso * da, b - paso * db
            if abs(na) <= 10 and 0 <= nb <= 10 and _logloss(_aplicar(pares, na, nb)) <= actual:
                break
            paso *= .5
        if paso <= 1e-8:
            break
        a, b = na, nb
        if abs(paso * da) < 1e-9 and abs(paso * db) < 1e-9:
            break
    return a, b


def ajustar_intercepto(pares, b_fijo, iteraciones=50):
    """Ajusta SOLO el intercepto A con la pendiente B fija (Newton 1D).
    B=1 => solo corrige el centro (subvaluacion del local), sin tocar la
    confianza. Es el ajuste de 1 parametro, mucho menos propenso a sobreajuste."""
    zs = [_logit(p) for p, _ in pares]
    ys = [y for _, y in pares]
    if not pares or len(set(ys)) < 2:
        return 0.0
    a = 0.0
    for _ in range(iteraciones):
        g = h = 0.0
        for z, y in zip(zs, ys):
            mu = _sigmoid(a + b_fijo * z)
            g += mu - y
            h += mu * (1 - mu)
        if h < 1e-12:
            break
        da = g / h
        actual = _logloss(_aplicar(pares, a, b_fijo))
        paso = 1.0
        while paso > 1e-8:
            nuevo = a - paso * da
            if abs(nuevo) <= 10 and _logloss(_aplicar(pares, nuevo, b_fijo)) <= actual:
                break
            paso *= .5
        if paso <= 1e-8:
            break
        a = nuevo
        if abs(paso * da) < 1e-9:
            break
    return a


def _brier(pares):
    return sum((p - y) ** 2 for p, y in pares) / len(pares)


def _logloss(pares):
    eps = 1e-9
    return sum(-(y * math.log(min(max(p, eps), 1 - eps))
                 + (1 - y) * math.log(min(max(1 - p, eps), 1 - eps)))
               for p, y in pares) / len(pares)


def _aplicar(pares, a, b):
    return [(_sigmoid(a + b * _logit(p)), y) for p, y in pares]


def _cargar_pares(desde=None):
    """Devuelve pares ML y F5 (prob_cruda, gano) en orden cronologico, mas los
    errores de total (esperado - real) para juego completo y F5."""
    preds = validar.cargar_predicciones()
    if desde:
        preds = [p for p in preds if validar._clave_fecha(p["fecha"]) >= validar._clave_fecha(desde)]
    fechas = sorted({p["fecha"] for p in preds}, key=validar._clave_fecha)
    cache = validar._cargar_cache()

    ml, f5 = [], []
    fechas_ml, fechas_f5 = [], []
    tot_pred, tot_real = [], []
    f5_pred, f5_real = [], []
    resultados = validar.preparar_resultados(fechas, cache)
    for fecha in fechas:
        reales = resultados[fecha]
        for p in (x for x in preds if x["fecha"] == fecha):
            real = validar.resultado_prediccion(p, reales)
            if not real:
                continue
            gano_casa = 1 if real["rc"] > real["rv"] else 0
            pc = validar._f(p, "p_casa")
            if pc is not None:
                ml.append((pc, gano_casa))
                fechas_ml.append(validar._clave_fecha(fecha))
            te = validar._f(p, "total_esp")
            if te is not None:
                tot_pred.append(te)
                tot_real.append(real["rv"] + real["rc"])
            if real["f5v"] is not None:
                pc5 = validar._f(p, "p_casa_f5")
                if pc5 is not None:
                    f5.append((pc5, 1 if real["f5c"] > real["f5v"] else 0))
                    fechas_f5.append(validar._clave_fecha(fecha))
                tf5 = validar._f(p, "total_f5")
                if tf5 is not None:
                    f5_pred.append(tf5)
                    f5_real.append(real["f5v"] + real["f5c"])
    validar._guardar_cache(cache)
    return ml, f5, (tot_pred, tot_real), (f5_pred, f5_real), fechas_ml, fechas_f5


def pliegues_temporales(fechas, k=5):
    """Ventana creciente por FECHA: ningun juego del mismo dia cruza el corte."""
    dias = sorted(set(fechas))
    if dias != list(dict.fromkeys(fechas)):
        raise ValueError("Fechas fuera de orden")
    if len(dias) < k + 1:
        raise ValueError("Fechas insuficientes para validacion temporal")
    bloques = [dias[len(dias)*j//(k+1):len(dias)*(j+1)//(k+1)] for j in range(k+1)]
    for bloque in bloques[1:]:
        train = [i for i, f in enumerate(fechas) if f < bloque[0]]
        test = [i for i, f in enumerate(fechas) if bloque[0] <= f <= bloque[-1]]
        yield train, test


def _cv_brier(pares, ajustador, k=5, fechas=None):
    fechas = fechas if fechas is not None else list(range(len(pares)))
    errores = []
    for train_i, test_i in pliegues_temporales(fechas, k):
        a, b = ajustador([pares[i] for i in train_i])
        errores.extend((_sigmoid(a + b * _logit(pares[i][0])) - pares[i][1])**2 for i in test_i)
    return sum(errores) / len(errores)


def _reporte_calibracion(nombre, pares, fechas):
    if len(pares) < 120 or len(set(fechas)) < 12:
        print(f"\n{nombre}: muestra insuficiente ({len(pares)}), no se ajusta.")
        return None
    # Ultimo 20% de fechas reservado; se selecciona candidato SOLO en desarrollo.
    dias = sorted(set(fechas))
    corte = dias[max(1, int(len(dias)*.8))]
    n_dev = sum(f < corte for f in fechas)
    dev, test = pares[:n_dev], pares[n_dev:]
    fechas_dev = fechas[:n_dev]
    opciones = [("Identidad", lambda tr: (0.0, 1.0))]
    for b in (1.0, .85, .70):
        opciones.append((f"Intercepto + B={b}", lambda tr, b=b: (ajustar_intercepto(tr, b), b)))
    opciones.append(("Platt", ajustar_platt))
    puntuados = [(nombre, aj, _cv_brier(dev, aj, fechas=fechas_dev)) for nombre, aj in opciones]
    mejor = min(puntuados, key=lambda x: x[2])
    a, b = mejor[1](dev)
    calibrados = _aplicar(test, a, b)
    base_rate = sum(y for _, y in dev) / len(dev)
    baseline = [(base_rate, y) for _, y in test]
    print(f"\n{nombre}: desarrollo={len(dev)}, holdout={len(test)} desde {corte}")
    for etiqueta, _, score in puntuados:
        print(f"  Desarrollo temporal {etiqueta}: Brier={score:.6f}")
    print(f"  Candidato seleccionado ANTES del holdout: {mejor[0]}, A={a:.6f}, B={b:.6f}")
    for etiqueta, datos in [("Crudo", test), ("Candidato", calibrados), ("Frecuencia local previa", baseline)]:
        print(f"  Holdout {etiqueta}: Brier={_brier(datos):.6f}, logloss={_logloss(datos):.6f}")
    print("  Diagnostico exploratorio: versiones mezcladas y legacy sin timestamp.")
    print("  No promueve parametros automaticamente. Requiere validacion prospectiva y mercado.")
    return a, b


def _reporte_totales(nombre, pred, real, base_actual):
    if not pred:
        return
    n = len(pred)
    mp = sum(pred) / n
    mr = sum(real) / n
    bias = sum(pr - re for pr, re in zip(pred, real)) / n
    factor = mr / mp if mp > 0 else 1.0
    print(f"\n{nombre}  (n={n})")
    print(f"  Media esperada {mp:.2f}  vs  real {mr:.2f}   bias {bias:+.3f}")
    print(f"  Razon observado/esperado = {factor:.4f}; descriptiva, no parametro recomendado.")
    print("  OJO: valido solo si estas predicciones se generaron con el AJUSTE_BASE")
    print("  actual. Si acabas de cambiarlo, el historico viejo aun trae el nivel")
    print("  anterior. No re-simules el pasado con datos actuales: recoge nuevas predicciones.")


def main(desde=None):
    ml, f5, (tp, tr), (f5p, f5r), fechas_ml, fechas_f5 = _cargar_pares(desde)
    print("=" * 64)
    print("           CALIBRACION DE PROBABILIDADES")
    print("=" * 64)
    print(f"Juegos con resultado: ML={len(ml)}  F5={len(f5)}")

    _reporte_calibracion("MONEYLINE (gana la casa)", ml, fechas_ml)
    _reporte_calibracion("F5 — gana la casa", f5, fechas_f5)

    print("\n" + "-" * 64)
    print("NIVEL DE TOTALES (diagnostico; tambien puede afectar ML)")
    _reporte_totales("TOTALES juego completo", tp, tr, AJUSTE_BASE)
    _reporte_totales("TOTALES F5", f5p, f5r, AJUSTE_BASE)

    print("\n" + "=" * 64)
    print("COMO LEERLO")
    print("  La capa sirve si baja el Brier/log-loss de TEST (fuera de muestra).")
    print("  No copies parametros sin confirmar mejora prospectiva contra baseline y mercado.")
    print("  No ajustes el nivel con un historico de versiones mezcladas.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
