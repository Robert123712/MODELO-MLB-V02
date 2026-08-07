# ============================================================
# ANALISIS POST-PARTIDO — simula juegos ya jugados y los compara
# contra el resultado real. Mide el error del modelo en un dia.
# Uso:  python analisis_comparacion.py [mm/dd/YYYY]
#
# Para medir CALIBRACION sobre todo el historico acumulado usa
# validar.py: este script es el detalle juego por juego de una fecha.
# ============================================================
import os
import sys

import numpy as np
import statsapi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import modelo_diario as m

FECHA = sys.argv[1] if len(sys.argv) > 1 else "06/18/2026"


def main():
    juegos = statsapi.schedule(date=FECHA)
    print(f"\n=== ANALISIS POST-PARTIDO: {FECHA} ===\n")

    finales = [j for j in juegos if j["status"] == "Final"
               and j.get("away_probable_pitcher") and j.get("home_probable_pitcher")]
    if not finales:
        print("No se encontraron juegos finalizados con abridores en esa fecha.")
        return 1

    frac_f5 = m.f5_frac_liga(FECHA)
    print(f"Juegos finalizados: {len(finales)} | F5 fraccion: {frac_f5:.3f}\n")

    encabezado = (f"{'VISITA':22s} {'CASA':22s} {'PRED':>6s} {'REAL':>5s} "
                  f"{'ERR':>6s} {'P_CASA':>7s} {'GANO':>5s} {'F5 PRED':>8s}")
    print(encabezado)
    print("=" * len(encabezado))

    resultados = []
    for j in finales:
        r = m.evaluar_juego(j, FECHA, frac_f5)
        if r is None:
            print(f"{j['away_name']:22s} {j['home_name']:22s} {'SIN DATOS':>6s}")
            continue

        rv = j.get("away_score", 0) or 0
        rc = j.get("home_score", 0) or 0
        real_total = rv + rc
        gano_casa = 1 if rc > rv else 0
        err_total = r["total_esp"] - real_total
        acerto = (r["p_casa"] > 0.5) == (gano_casa == 1)

        resultados.append({
            "visita": r["visita"], "casa": r["casa"],
            "pred_total": r["total_esp"], "real_total": real_total,
            "err_total": err_total, "p_casa": r["p_casa"],
            "gano_casa": gano_casa, "acerto_ml": acerto,
            "f5_total": r["f5"]["total_esp"],
        })

        print(f"{r['visita']:22s} {r['casa']:22s} {r['total_esp']:6.2f} {real_total:5d} "
              f"{err_total:+6.2f} {r['p_casa']:6.1%} {'SI' if acerto else 'NO':>5s} "
              f"{r['f5']['total_esp']:8.2f}")

    if not resultados:
        print("\nNingun juego pudo evaluarse.")
        return 1

    errores = np.array([r["err_total"] for r in resultados])
    n = len(resultados)
    aciertos = sum(r["acerto_ml"] for r in resultados)

    print("\n" + "=" * len(encabezado))
    print("RESUMEN")
    print(f"  MAE Total (carreras):        {np.abs(errores).mean():.3f}")
    print(f"  RMSE Total:                  {np.sqrt((errores ** 2).mean()):.3f}")
    bias = errores.mean()
    print(f"  Bias (pred - real):          {bias:+.3f}  "
          f"({'sobreestima' if bias > 0 else 'subestima'})")
    print(f"  Desv. std del error:         {errores.std():.3f}")
    print(f"  Aciertos ML (favorito):      {aciertos}/{n} ({aciertos / n:.0%})")
    for umbral in (1, 2, 3):
        dentro = int((np.abs(errores) <= umbral).sum())
        print(f"  |error| <= {umbral}:               {dentro}/{n} ({dentro / n:.0%})")

    print("\n=== FIN ANALISIS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
