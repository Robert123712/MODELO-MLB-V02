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
        a -= da
        b -= db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break
    return a, b


def ajustar_intercepto(pares, b_fijo, iteraciones=50):
    """Ajusta SOLO el intercepto A con la pendiente B fija (Newton 1D).
    B=1 => solo corrige el centro (subvaluacion del local), sin tocar la
    confianza. Es el ajuste de 1 parametro, mucho menos propenso a sobreajuste."""
    zs = [_logit(p) for p, _ in pares]
    ys = [y for _, y in pares]
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
        a -= da
        if abs(da) < 1e-9:
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
    with open(validar.ARCHIVO_PRED, encoding="utf-8") as f:
        preds = list(csv.DictReader(f))
    if desde:
        preds = [p for p in preds if validar._clave_fecha(p["fecha"]) >= validar._clave_fecha(desde)]
    fechas = sorted({p["fecha"] for p in preds}, key=validar._clave_fecha)
    cache = validar._cargar_cache()

    ml, f5 = [], []
    tot_pred, tot_real = [], []
    f5_pred, f5_real = [], []
    for fecha in fechas:
        reales = validar.resultados_de_fecha(fecha, cache)
        for p in (x for x in preds if x["fecha"] == fecha):
            real = reales.get((p["visita"], p["casa"]))
            if not real:
                continue
            gano_casa = 1 if real["rc"] > real["rv"] else 0
            pc = validar._f(p, "p_casa")
            if pc is not None:
                ml.append((pc, gano_casa))
            te = validar._f(p, "total_esp")
            if te is not None:
                tot_pred.append(te)
                tot_real.append(real["rv"] + real["rc"])
            if real["f5v"] is not None:
                pc5 = validar._f(p, "p_casa_f5")
                if pc5 is not None:
                    f5.append((pc5, 1 if real["f5c"] > real["f5v"] else 0))
                tf5 = validar._f(p, "total_f5")
                if tf5 is not None:
                    f5_pred.append(tf5)
                    f5_real.append(real["f5v"] + real["f5c"])
    validar._guardar_cache(cache)
    return ml, f5, (tot_pred, tot_real), (f5_pred, f5_real)


def _cv_brier(pares, ajustador, k=5):
    """Brier PROMEDIO fuera de muestra por validacion cruzada de k pliegues.
    'ajustador' recibe el train de cada pliegue y devuelve (A, B); se evalua en
    el pliegue retenido. Baraja de forma determinista para no depender del orden."""
    idx = list(range(len(pares)))
    # barajado determinista (sin semilla externa): intercala por resto
    idx.sort(key=lambda i: (i * 2654435761) % len(pares))
    folds = [idx[j::k] for j in range(k)]
    err = 0.0
    for j in range(k):
        test_i = set(folds[j])
        train = [pares[i] for i in idx if i not in test_i]
        test = [pares[i] for i in folds[j]]
        a, b = ajustador(train)
        err += sum((_sigmoid(a + b * _logit(p)) - y) ** 2 for p, y in test)
    return err / len(pares)


def _reporte_calibracion(nombre, pares):
    if len(pares) < 60:
        print(f"\n{nombre}: muestra chica ({len(pares)}), no se ajusta.")
        return None

    base = _brier(pares)
    print(f"\n{nombre}  (n={len(pares)})   Brier crudo = {base:.4f}")
    print("  Validacion cruzada de 5 pliegues (Brier fuera de muestra):")

    opciones = []   # (etiqueta, ajustador_full, cv)
    # 1) intercepto solo (B fijo): corrige el CENTRO, 1 parametro
    for b in (1.0, 0.85, 0.70):
        aj = (lambda bb: (lambda tr: (ajustar_intercepto(tr, bb), bb)))(b)
        cv = _cv_brier(pares, aj)
        a_full = ajustar_intercepto(pares, b)
        opciones.append((f"A libre, B={b:.2f}", a_full, b, cv))
    # 2) ajuste completo (A y B libres): 2 parametros
    cv_full = _cv_brier(pares, ajustar_platt)
    a2, b2 = ajustar_platt(pares)
    opciones.append(("A y B libres", a2, b2, cv_full))

    mejor = min(opciones, key=lambda o: o[3])
    for etq, a, b, cv in opciones:
        gana = "  <- mejor" if (etq, a, b, cv) == mejor else ""
        signo = "MEJORA" if cv < base else "peor  "
        print(f"    {etq:16s} A={a:+.3f} B={b:.2f}   CV Brier {cv:.4f} ({signo} vs {base:.4f}){gana}")

    if mejor[3] < base - 0.0005:   # margen minimo para no perseguir ruido
        print(f"  -> USAR: ML_CAL_A={mejor[1]:+.4f}  ML_CAL_B={mejor[2]:.4f}")
        return mejor[1], mejor[2]
    print("  -> NINGUNA capa mejora fuera de muestra de forma robusta. Dejar identidad (A=0, B=1).")
    return 0.0, 1.0


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
    print(f"  Nivel * {factor:.4f} centra el sesgo.  "
          f"AJUSTE_BASE {base_actual:.3f} -> {base_actual * factor:.3f}")


def main(desde=None):
    ml, f5, (tp, tr), (f5p, f5r) = _cargar_pares(desde)
    print("=" * 64)
    print("           CALIBRACION DE PROBABILIDADES")
    print("=" * 64)
    print(f"Juegos con resultado: ML={len(ml)}  F5={len(f5)}")

    _reporte_calibracion("MONEYLINE (gana la casa)", ml)
    _reporte_calibracion("F5 — gana la casa", f5)

    print("\n" + "-" * 64)
    print("NIVEL DE TOTALES (independiente del ML: se cancela en el cociente)")
    _reporte_totales("TOTALES juego completo", tp, tr, AJUSTE_BASE)
    _reporte_totales("TOTALES F5", f5p, f5r, AJUSTE_BASE)

    print("\n" + "=" * 64)
    print("COMO LEERLO")
    print("  La capa sirve si baja el Brier/log-loss de TEST (fuera de muestra).")
    print("  Pega A y B de 'todo' en ML_CAL_A / ML_CAL_B de modelo_diario.py.")
    print("  Para totales, mueve AJUSTE_BASE al valor sugerido (afecta solo el")
    print("  nivel de carreras; el moneyline no se mueve).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
