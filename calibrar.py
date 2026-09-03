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


def _reporte_calibracion(nombre, pares, frac_train=0.7):
    if len(pares) < 60:
        print(f"\n{nombre}: muestra chica ({len(pares)}), no se ajusta.")
        return None
    corte = int(len(pares) * frac_train)
    train, test = pares[:corte], pares[corte:]

    a_tr, b_tr = ajustar_platt(train)          # ajuste honesto: solo con train
    a_full, b_full = ajustar_platt(pares)      # el que se usaria en produccion

    print(f"\n{nombre}  (n={len(pares)}; train={len(train)}, test={len(test)})")
    print(f"  Ajuste (train):   A={a_tr:+.4f}  B={b_tr:.4f}")
    print(f"  Ajuste (todo):    A={a_full:+.4f}  B={b_full:.4f}   <- pegar en modelo_diario.py")

    def linea(etq, ps):
        cal = _aplicar(ps, a_tr, b_tr)
        print(f"  {etq:16s} Brier {_brier(ps):.4f} -> {_brier(cal):.4f}   "
              f"log-loss {_logloss(ps):.4f} -> {_logloss(cal):.4f}")

    print("  Efecto de la capa ajustada en TRAIN sobre cada bloque:")
    linea("  train (in)", train)
    linea("  test (out)", test)    # <- la prueba que importa: fuera de muestra
    return a_full, b_full


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
