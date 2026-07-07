# ============================================================
# Compara predicciones guardadas vs resultados reales
# Sin re-simular -- solo cruza predicciones.csv con resultados MLB
# ============================================================
import statsapi
import sys

csv_file = "predicciones.csv"

# Leer predicciones
with open(csv_file, encoding="utf-8") as f:
    lineas = f.readlines()

headers = lineas[0].strip().split(",")
preds = [dict(zip(headers, l.strip().split(","))) for l in lineas[1:]]

print(f"{'FECHA':12s} {'VISITA':22s} {'CASA':22s} {'LAM_V':6s} {'LAM_C':6s} {'TOT_ESP':7s} {'P_CASA':7s} {'REAL_V':6s} {'REAL_C':6s} {'TOT_REAL':7s} {'ERR_TOT':7s} {'GANA?':5s}")
print("=" * 110)

for p in preds:
    fecha = p["fecha"]
    visita = p["visita"]
    casa = p["casa"]
    lam_v = float(p["lam_v"])
    lam_c = float(p["lam_c"])
    tot_esp = float(p["total_esp"])
    p_casa = float(p["p_casa"])

    # Obtener resultado real
    try:
        juegos = statsapi.schedule(date=fecha)
    except Exception as e:
        print(f"  Error API para {fecha}: {e}")
        continue

    encontrado = None
    for j in juegos:
        if j["status"] == "Final" and j["away_name"] == visita and j["home_name"] == casa:
            encontrado = j
            break

    if not encontrado:
        print(f"{fecha:12s} {visita:22s} {casa:22s} -- No se encontro resultado real")
        continue

    rv = encontrado.get("away_score", 0) or 0
    rc = encontrado.get("home_score", 0) or 0
    real_total = rv + rc
    err_tot = tot_esp - real_total
    gana_casa_real = 1 if rc > rv else 0
    acerto = (p_casa > 0.5) == (gana_casa_real == 1)

    print(f"{fecha:12s} {visita:22s} {casa:22s} {lam_v:5.2f}  {lam_c:5.2f}  {tot_esp:6.2f}   {p_casa:6.1%}  "
          f"{rv:3d}    {rc:3d}    {real_total:3d}     {err_tot:+5.2f}  {'SI' if acerto else 'NO'}")

print("\n=== FIN ===")
