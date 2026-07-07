# ============================================================
# ANALISIS: Simula partidos ya jugados y compara vs resultado real
# ============================================================
import statsapi
import numpy as np
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))

import modelo_diario as m
import valor as v

FECHA = sys.argv[1] if len(sys.argv) > 1 else "06/18/2026"

# --- 1. Obtener schedule de ese dia (incluye resultados) ---
juegos = statsapi.schedule(date=FECHA)
print(f"\n=== ANALISIS POST-PARTIDO: {FECHA} ===\n")

# --- 2. Separar los juegos finalizados (los que tienen resultado) ---
finales = [j for j in juegos if j["status"] == "Final"]

if not finales:
    print("No se encontraron juegos finalizados en esa fecha.")
    sys.exit(1)

print(f"Juegos finalizados: {len(finales)}\n")

# --- 3. Correr el modelo para cada juego finalizado ---
# Necesitamos forzar el filtro para que acepte "Final" y use los pitchers
# Tambien necesitamos las carreras reales
modelables = [j for j in finales
              if j.get("away_probable_pitcher") and j.get("home_probable_pitcher")]

_frac_f5 = m.f5_frac_liga(FECHA)

print(f"F5 fraccion: {_frac_f5:.3f}\n")
print(f"{'VISITA':22s} {'CASA':22s} {'PRED V':6s} {'PRED C':6s} {'PRED TOT':8s} {'REAL V':6s} {'REAL C':6s} {'REAL TOT':8s} {'ERR TOT':7s} {'ERR ML':7s}")
print("=" * 100)

resultados = []

for j in modelables:
    visita, casa = j["away_name"], j["home_name"]
    p_v, p_c = j["away_probable_pitcher"], j["home_probable_pitcher"]
    rv = j.get("away_score", 0) or 0
    rc = j.get("home_score", 0) or 0
    real_total = rv + rc
    real_gana_casa = 1 if rc > rv else 0

    pv = m.datos_pitcher(p_v)
    pc = m.datos_pitcher(p_c)
    if pv is None or pc is None or pv["fip"] is None or pc["fip"] is None:
        print(f"{visita:22s} {casa:22s} {'SIN DATOS':^45s}")
        continue

    fip_v = m.fip_blend(pv)
    fip_c = m.fip_blend(pc)
    ip_v, ip_c = pv["ip_esp"], pc["ip_esp"]
    mano_v, mano_c = pv["mano"], pc["mano"]

    rg_v = m.carreras_por_juego(visita, FECHA)
    rg_c = m.carreras_por_juego(casa, FECHA)
    park = m.PARK.get(casa, 1.00)

    split_v = m.split_ofensivo(visita, mano_c)
    split_c = m.split_ofensivo(casa, mano_v)

    bp_v = m.bullpen_stats(visita)
    bp_c = m.bullpen_stats(casa)

    kbb_c = m.factor_kbb_comb(pc, bp_c)
    kbb_v = m.factor_kbb_comb(pv, bp_v)
    def_v = m.factor_defensivo(visita)
    def_c = m.factor_defensivo(casa)
    cal_v = m.factor_calibracion(visita, FECHA)
    cal_c = m.factor_calibracion(casa, FECHA)

    pitcheo_c = m.fip_combinado(fip_c, ip_c, bp_c["fip"])
    pitcheo_v = m.fip_combinado(fip_v, ip_v, bp_v["fip"])

    lam_v = rg_v * split_v * m.multiplicador_pitcheo(pitcheo_c) * kbb_c * def_c * park * m.AJUSTE_BASE * cal_v
    lam_c = rg_c * split_c * m.multiplicador_pitcheo(pitcheo_v) * kbb_v * def_v * park * m.AJUSTE_BASE * m.HFA * cal_c

    overs, p_casa, p_casa_rl = m.simular(lam_v, lam_c)

    pred_total = lam_v + lam_c
    err_total = pred_total - real_total
    err_ml = p_casa - real_gana_casa

    monev = 1 - p_casa
    monec = p_casa

    resultados.append({
        "visita": visita, "casa": casa,
        "lam_v": lam_v, "lam_c": lam_c,
        "pred_total": pred_total,
        "real_v": rv, "real_c": rc,
        "real_total": real_total,
        "err_total": err_total,
        "p_casa": p_casa,
        "real_gana_casa": real_gana_casa,
        "err_ml": err_ml,
        "p_casa_rl": p_casa_rl,
    })

    print(f"{visita:22s} {casa:22s} {lam_v:5.2f}  {lam_c:5.2f}  {pred_total:6.2f}     "
          f"{rv:3d}    {rc:3d}    {real_total:3d}       {err_total:+5.2f}   {err_ml:+5.3f}")

# --- 4. Resumen de errores ---
print("\n" + "=" * 100)
print("RESUMEN DE ERRORES")
print("=" * 100)

errores_total = [r["err_total"] for r in resultados]
errores_ml = [r["err_ml"] for r in resultados]
real_totales = [r["real_total"] for r in resultados]
pred_totales = [r["pred_total"] for r in resultados]

if resultados:
    mae_total = np.mean(np.abs(errores_total))
    rmse_total = np.sqrt(np.mean(np.array(errores_total) ** 2))
    bias_total = np.mean(errores_total)
    mae_ml = np.mean(np.abs(errores_ml))
    cal_ml = np.mean([1 if (r["p_casa"] > 0.5) == (r["real_gana_casa"] == 1) else 0 for r in resultados])

    print(f"  MAE Total (carreras):          {mae_total:.3f}")
    print(f"  RMSE Total:                    {rmse_total:.3f}")
    print(f"  Bias (pred - real):            {bias_total:+5.3f}  ({'sobreestima' if bias_total > 0 else 'subestima'} total)")
    print(f"  MAE Moneyline:                 {mae_ml:.3f}")
    print(f"  Aciertos ML (favorito >50%):   {cal_ml:.0%} ({int(cal_ml * len(resultados))}/{len(resultados)})")

    # Desglose: juegos donde el modelo acerto/fallo la direccion ML
    print(f"\n  Desglose individual:")
    for r in resultados:
        acerto_ml = (r["p_casa"] > 0.5) == (r["real_gana_casa"] == 1)
        fav = r["casa"] if r["p_casa"] > 0.5 else r["visita"]
        print(f"    {r['visita']:22s} @ {r['casa']:22s} | "
              f"ML pred: {r['casa']} {r['p_casa']:.1%} | "
              f"Real: {r['real_v']}-{r['real_c']} | "
              f"{'✅' if acerto_ml else '❌'} ML")

    # Distribucion de errores
    print(f"\n  Errores total (carreras):")
    print(f"    Min: {min(errores_total):+.2f}  Max: {max(errores_total):+.2f}")
    print(f"    Desv std: {np.std(errores_total):.3f}")

    # Cuantas veces quedo el total real dentro del rango esperado
    dentro_1 = sum(1 for r in resultados if abs(r["err_total"]) <= 1)
    dentro_2 = sum(1 for r in resultados if abs(r["err_total"]) <= 2)
    dentro_3 = sum(1 for r in resultados if abs(r["err_total"]) <= 3)
    n = len(resultados)
    print(f"    |error| <= 1: {dentro_1}/{n} ({dentro_1/n:.0%})")
    print(f"    |error| <= 2: {dentro_2}/{n} ({dentro_2/n:.0%})")
    print(f"    |error| <= 3: {dentro_3}/{n} ({dentro_3/n:.0%})")

    # Analisis F5
    print(f"\n  {'F5':>72s}")
    print(f"  {'VISITA':22s} {'CASA':22s} {'LAM_V_F5':8s} {'LAM_C_F5':8s} {'REAL V':6s} {'REAL C':6s}")
    for j, r in zip(modelables, resultados):
        visita, casa = r["visita"], r["casa"]
        p_v, p_c = j["away_probable_pitcher"], j["home_probable_pitcher"]
        pv = m.datos_pitcher(p_v)
        pc = m.datos_pitcher(p_c)
        if pv is None or pc is None or pv["fip"] is None or pc["fip"] is None:
            continue
        fip_v = m.fip_blend(pv)
        fip_c = m.fip_blend(pc)
        ip_v, ip_c = pv["ip_esp"], pc["ip_esp"]
        bp_v = m.bullpen_stats(visita)
        bp_c = m.bullpen_stats(casa)
        pitcheo_c_f5 = m.fip_f5(fip_c, ip_c, bp_c["fip"])
        pitcheo_v_f5 = m.fip_f5(fip_v, ip_v, bp_v["fip"])
        lam_v_f5 = rg_v * split_v * _frac_f5 * m.multiplicador_pitcheo(pitcheo_c_f5) * kbb_c * def_c * park * m.AJUSTE_BASE * cal_v
        lam_c_f5 = rg_c * split_c * _frac_f5 * m.multiplicador_pitcheo(pitcheo_v_f5) * kbb_v * def_v * park * m.AJUSTE_BASE * m.HFA * cal_c
        # Nota: no tenemos el linescore real F5 desde el schedule, solo el final
        print(f"  {visita:22s} {casa:22s} {lam_v_f5:6.2f}  {lam_c_f5:6.2f}")

print(f"\n=== FIN ANALISIS ===")
