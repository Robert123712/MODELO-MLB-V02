# =============================================================
# GENERADOR DE EXCEL — Predicciones MLB con formato profesional
# Uso:  python -u generar_excel.py [fecha]
# Ej:   python -u generar_excel.py 07/02/2026
# =============================================================

import sys, os
sys.stdout.reconfigure(encoding="utf-8")

from datetime import date
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, numbers
from openpyxl.utils import get_column_letter
from openpyxl.chart import BarChart, Reference

import modelo_diario as m

# ── Estilos ──
AZUL = "1F4E79"
AZUL_CLARO = "D6E4F0"
VERDE = "C6EFCE"
ROJO = "FFC7CE"
AMARILLO = "FFEB9C"
GRIS = "F2F2F2"
BLANCO = "FFFFFF"

header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
header_fill = PatternFill("solid", fgColor=AZUL)
subheader_font = Font(name="Calibri", bold=True, color=AZUL, size=10)
data_font = Font(name="Calibri", size=10)
bold_font = Font(name="Calibri", bold=True, size=10)
title_font = Font(name="Calibri", bold=True, color=AZUL, size=14)
pct_fmt = '0.0%'
dec_fmt = '0.00'
thin_border = Border(
    left=Side(style="thin", color="BFBFBF"),
    right=Side(style="thin", color="BFBFBF"),
    top=Side(style="thin", color="BFBFBF"),
    bottom=Side(style="thin", color="BFBFBF"),
)
fill_par = PatternFill("solid", fgColor=GRIS)

def prob_a_momio(p):
    if p <= 0.01 or p >= 0.99:
        return "—"
    if p >= 0.5:
        return f"{-round(100 * p / (1 - p))}"
    return f"+{round(100 * (1 - p) / p)}"

def estilo_celda(ws, row, col, value, font=data_font, fill=None, fmt=None, align=None):
    cell = ws.cell(row=row, column=col, value=value)
    cell.font = font
    cell.border = thin_border
    if fill:
        cell.fill = fill
    if fmt:
        cell.number_format = fmt
    if align:
        cell.alignment = align
    return cell

def escribir_encabezados(ws, row, headers, fill=header_fill, font=header_font):
    for i, h in enumerate(headers, 1):
        cell = ws.cell(row=row, column=i, value=h)
        cell.font = font
        cell.fill = fill
        cell.border = thin_border
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

def colorear_overs(ws, row, col, p):
    cell = ws.cell(row=row, column=col)
    if p >= 0.65:
        cell.fill = PatternFill("solid", fgColor=VERDE)
    elif p <= 0.35:
        cell.fill = PatternFill("solid", fgColor=ROJO)
    else:
        cell.fill = PatternFill("solid", fgColor=AMARILLO)

# ══════════════════════════════════════════════════════════════

def generar(fecha=None):
    hoy = fecha or date.today().strftime("%m/%d/%Y")

    print(f"📡 Obteniendo datos para {hoy}...", flush=True)
    juegos = m.statsapi.schedule(date=hoy)
    modelables = [j for j in juegos if j["status"] in ("Scheduled", "Pre-Game", "Warmup")
                  and j["away_probable_pitcher"] and j["home_probable_pitcher"]]

    if not modelables:
        print("⚠ No hay juegos modelables para esta fecha.", flush=True)
        return

    print(f"✅ {len(modelables)} juegos encontrados. Procesando...", flush=True)

    _frac_f5 = m.f5_frac_liga(hoy)

    wb = openpyxl.Workbook()
    # ── SHEET 1: RESUMEN ──
    ws1 = wb.active
    ws1.title = "Resumen"
    ws1.sheet_properties.tabColor = AZUL

    # Título
    ws1.merge_cells("A1:N1")
    titulo = ws1.cell(row=1, column=1, value=f"⚾ PREDICCIONES MLB — {hoy}")
    titulo.font = title_font
    ws1.row_dimensions[1].height = 30

    # Parámetros
    ws1.merge_cells("A2:N2")
    ws1.cell(row=2, column=1, value=f"Calibración: amortigua={m.AMORTIGUA} | dispersion_k={m.DISPERSION_K} | base={m.AJUSTE_BASE} | F5 frac={_frac_f5:.3f} | Sims={m.N_SIMS:,}").font = Font(name="Calibri", italic=True, color="666666", size=9)

    fila = 4
    headers = [
        "Visitante", "Casa", "Abridor V", "Abridor C",
        "FIP V", "FIP C", "IP V", "IP C",
        "Bullpen V", "Bullpen C",
        "RG V", "RG C", "Split V", "Split C", "Park",
        "DEF V", "DEF C",
        "λ V", "λ C", "Total λ",
        "ML V %", "ML V Momio", "ML C %", "ML C Momio",
        "RL V +1.5", "RL C -1.5",
        "O5.5", "O6.5", "O7.5", "O8.5", "O9.5", "O10.5"
    ]
    escribir_encabezados(ws1, fila, headers)
    ws1.row_dimensions[fila].height = 35

    data_fila = fila + 1
    total_slate = 0

    for idx, j in enumerate(modelables):
        visita, casa = j["away_name"], j["home_name"]
        p_v, p_c = j["away_probable_pitcher"], j["home_probable_pitcher"]

        pv = m.datos_pitcher(p_v)
        pc = m.datos_pitcher(p_c)
        if pv is None or pc is None or pv["fip"] is None or pc["fip"] is None:
            continue

        fip_v = m.fip_blend(pv)
        fip_c = m.fip_blend(pc)
        ip_v, ip_c = pv["ip_esp"], pc["ip_esp"]
        mano_v, mano_c = pv["mano"], pc["mano"]

        rg_v = m.carreras_por_juego(visita, hoy)
        rg_c = m.carreras_por_juego(casa, hoy)
        park = m.PARK.get(casa, 1.00)
        split_v = m.split_ofensivo(visita, mano_c)
        split_c = m.split_ofensivo(casa, mano_v)

        bp_v = m.bullpen_stats(visita)
        bp_c = m.bullpen_stats(casa)

        def_v = m.factor_defensivo(visita)
        def_c = m.factor_defensivo(casa)

        pitcheo_c = m.fip_combinado(fip_c, ip_c, bp_c["fip"])
        pitcheo_v = m.fip_combinado(fip_v, ip_v, bp_v["fip"])

        lam_v = rg_v * split_v * m.multiplicador_pitcheo(pitcheo_c) * def_c * park * m.AJUSTE_BASE
        lam_c = rg_c * split_c * m.multiplicador_pitcheo(pitcheo_v) * def_v * park * m.AJUSTE_BASE * m.HFA
        total_slate += lam_v + lam_c

        overs, p_casa, p_casa_rl = m.simular(lam_v, lam_c)

        row = data_fila + idx
        f = data_font
        alt_fill = PatternFill("solid", fgColor=GRIS) if idx % 2 == 0 else None

        datos = [
            visita, casa, p_v, p_c,
            round(fip_v, 2), round(fip_c, 2), round(ip_v, 1), round(ip_c, 1),
            round(bp_v["fip"], 2), round(bp_c["fip"], 2),
            round(rg_v, 2), round(rg_c, 2), round(split_v, 3), round(split_c, 3), park,
            round(def_v, 3), round(def_c, 3),
            round(lam_v, 2), round(lam_c, 2), round(lam_v + lam_c, 2),
            round(1 - p_casa, 4), prob_a_momio(1 - p_casa),
            round(p_casa, 4), prob_a_momio(p_casa),
            round(1 - p_casa_rl, 4), round(p_casa_rl, 4),
            round(overs[5.5], 4), round(overs[6.5], 4), round(overs[7.5], 4),
            round(overs[8.5], 4), round(overs[9.5], 4), round(overs[10.5], 4),
        ]

        for col, val in enumerate(datos, 1):
            cell = ws1.cell(row=row, column=col, value=val)
            cell.font = f
            cell.border = thin_border
            if alt_fill:
                cell.fill = alt_fill
            if isinstance(val, float) and col >= 27:  # overs (tras quitar KBB, corren 2 cols)
                cell.number_format = pct_fmt
                colorear_overs(ws1, row, col, val)
            elif isinstance(val, float):
                cell.number_format = dec_fmt if col not in (15,) else dec_fmt
            if col in (21, 23):  # ML %
                cell.number_format = pct_fmt
            if col in (22, 24):  # Momios
                cell.alignment = Alignment(horizontal="center")

    # Anchos de columna
    anchos = [20, 20, 18, 18, 7, 7, 6, 6, 9, 9, 7, 7, 7, 7, 5,
              7, 7, 7, 7, 8, 9, 9, 9, 9, 9, 9, 6, 6, 6, 6, 6, 6]
    for i, a in enumerate(anchos, 1):
        ws1.column_dimensions[get_column_letter(i)].width = a
    ws1.freeze_panes = ws1.cell(row=fila + 1, column=1)

    # Total slate
    if len(modelables) > 0:
        prom = total_slate / len(modelables)
        fin_fila = data_fila + len(modelables)
        ws1.merge_cells(f"A{fin_fila}:G{fin_fila}")
        cel = ws1.cell(row=fin_fila, column=1, value=f"📊 Promedio total del slate: {prom:.2f} carreras")
        cel.font = Font(name="Calibri", bold=True, italic=True, size=10)

    # ── SHEET 2: DETALLE X JUEGO ──
    ws2 = wb.create_sheet("Detalle por Juego")
    ws2.sheet_properties.tabColor = "2E75B6"

    fila2 = 1
    for idx, j in enumerate(modelables):
        visita, casa = j["away_name"], j["home_name"]
        p_v, p_c = j["away_probable_pitcher"], j["home_probable_pitcher"]

        pv = m.datos_pitcher(p_v)
        pc = m.datos_pitcher(p_c)
        if pv is None or pc is None or pv["fip"] is None or pc["fip"] is None:
            continue

        fip_v = m.fip_blend(pv)
        fip_c = m.fip_blend(pc)
        ip_v, ip_c = pv["ip_esp"], pc["ip_esp"]
        mano_v, mano_c = pv["mano"], pc["mano"]

        rg_v = m.carreras_por_juego(visita, hoy)
        rg_c = m.carreras_por_juego(casa, hoy)
        park = m.PARK.get(casa, 1.00)
        split_v = m.split_ofensivo(visita, mano_c)
        split_c = m.split_ofensivo(casa, mano_v)

        bp_v = m.bullpen_stats(visita)
        bp_c = m.bullpen_stats(casa)

        def_v = m.factor_defensivo(visita)
        def_c = m.factor_defensivo(casa)

        pitcheo_c = m.fip_combinado(fip_c, ip_c, bp_c["fip"])
        pitcheo_v = m.fip_combinado(fip_v, ip_v, bp_v["fip"])

        lam_v = rg_v * split_v * m.multiplicador_pitcheo(pitcheo_c) * def_c * park * m.AJUSTE_BASE
        lam_c = rg_c * split_c * m.multiplicador_pitcheo(pitcheo_v) * def_v * park * m.AJUSTE_BASE * m.HFA

        overs, p_casa, p_casa_rl = m.simular(lam_v, lam_c)

        pitcheo_c_f5 = m.fip_f5(fip_c, ip_c, bp_c["fip"])
        pitcheo_v_f5 = m.fip_f5(fip_v, ip_v, bp_v["fip"])
        lam_v_f5 = rg_v * split_v * _frac_f5 * m.multiplicador_pitcheo(pitcheo_c_f5) * def_c * park * m.AJUSTE_BASE
        lam_c_f5 = rg_c * split_c * _frac_f5 * m.multiplicador_pitcheo(pitcheo_v_f5) * def_v * park * m.AJUSTE_BASE * m.HFA
        overs_f5, p_casa_f5, p_visita_f5, p_empate_f5 = m.simular_f5(lam_v_f5, lam_c_f5)

        # ── CABECERA DEL JUEGO ──
        ws2.merge_cells(f"A{fila2}:J{fila2}")
        cell = ws2.cell(row=fila2, column=1, value=f"{visita} @ {casa}  |  {p_v} vs {p_c}")
        cell.font = Font(name="Calibri", bold=True, color="FFFFFF", size=12)
        cell.fill = PatternFill("solid", fgColor=AZUL)
        cell.alignment = Alignment(horizontal="left", vertical="center")
        ws2.row_dimensions[fila2].height = 28
        fila2 += 1

        # Subencabezados
        sub1 = ["Métrica", "Visitante", "Casa"]
        escribir_encabezados(ws2, fila2, sub1, fill=PatternFill("solid", fgColor=AZUL_CLARO), font=subheader_font)
        fila2 += 1

        def par(fila, label, val_v, val_c, fmt_override=None):
            cell_a = estilo_celda(ws2, fila, 1, label, font=bold_font)
            cell_b = estilo_celda(ws2, fila, 2, val_v, fmt=fmt_override)
            cell_c = estilo_celda(ws2, fila, 3, val_c, fmt=fmt_override)
            if fila % 2 == 0:
                for c in [cell_a, cell_b, cell_c]:
                    c.fill = fill_par

        p_visita = 1 - p_casa
        par(fila2, "FIP", round(fip_v, 2), round(fip_c, 2)); fila2 += 1
        par(fila2, "IP Esperadas", round(ip_v, 1), round(ip_c, 1)); fila2 += 1
        par(fila2, "Mano", mano_v or "?", mano_c or "?"); fila2 += 1
        par(fila2, "Bullpen FIP", round(bp_v["fip"], 2), round(bp_c["fip"], 2)); fila2 += 1
        par(fila2, "R/G (reciencia)", round(rg_v, 2), round(rg_c, 2)); fila2 += 1
        par(fila2, "Split vs Mano", round(split_v, 3), round(split_c, 3)); fila2 += 1
        par(fila2, "Factor DEF", round(def_v, 3), round(def_c, 3)); fila2 += 1
        par(fila2, "Carreras Esperadas (λ)", round(lam_v, 2), round(lam_c, 2)); fila2 += 1
        par(fila2, "Total Esperado", round(lam_v + lam_c, 2), "—"); fila2 += 1
        par(fila2, "Park Factor", park, "—"); fila2 += 1
        par(fila2, "Moneyline Prob", round(p_visita, 4), round(p_casa, 4), pct_fmt); fila2 += 1
        par(fila2, "Moneyline Momio", prob_a_momio(p_visita), prob_a_momio(p_casa)); fila2 += 1
        par(fila2, "Run Line +1.5 / -1.5", round(1 - p_casa_rl, 4), round(p_casa_rl, 4), pct_fmt); fila2 += 1

        fila2 += 1

        # Overs
        ws2.cell(row=fila2, column=1, value="Probabilidades Overs").font = bold_font
        escribir_encabezados(ws2, fila2, ["", "O5.5", "O6.5", "O7.5", "O8.5", "O9.5", "O10.5", "O11.5", "O12.5"],
                          fill=PatternFill("solid", fgColor=AZUL_CLARO), font=subheader_font)
        fila2 += 1
        ovs = [overs[ln] for ln in [5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5]]
        estilo_celda(ws2, fila2, 1, "Over", font=bold_font)
        for ci, (ln, p) in enumerate([(ln, overs[ln]) for ln in [5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5]]):
            cel = estilo_celda(ws2, fila2, ci + 2, round(p, 4), fmt=pct_fmt)
            colorear_overs(ws2, fila2, ci + 2, p)
        fila2 += 1
        estilo_celda(ws2, fila2, 1, "Under", font=bold_font)
        for ci, (ln, p) in enumerate([(ln, overs[ln]) for ln in [5.5, 6.5, 7.5, 8.5, 9.5, 10.5, 11.5, 12.5]]):
            u = 1 - p
            cel = estilo_celda(ws2, fila2, ci + 2, round(u, 4), fmt=pct_fmt)
            colorear_overs(ws2, fila2, ci + 2, u)
        fila2 += 2

        # F5
        ws2.cell(row=fila2, column=1, value="Primeras 5 Entradas (F5)").font = Font(name="Calibri", bold=True, color="2E75B6", size=11)
        fila2 += 1
        sub_f5 = ["Métrica", "Valor"]
        escribir_encabezados(ws2, fila2, sub_f5, fill=PatternFill("solid", fgColor=AZUL_CLARO), font=subheader_font)
        fila2 += 1
        par_f5 = [
            ("Carreras λ V", round(lam_v_f5, 2)),
            ("Carreras λ C", round(lam_c_f5, 2)),
            ("Total F5", round(lam_v_f5 + lam_c_f5, 2)),
            ("ML Casa", f"{p_casa_f5:.1%} ({prob_a_momio(p_casa_f5)})"),
            ("ML Visita", f"{p_visita_f5:.1%} ({prob_a_momio(p_visita_f5)})"),
            ("Empate", f"{p_empate_f5:.1%} ({prob_a_momio(p_empate_f5)})"),
            ("RL Casa -0.5", f"{p_casa_f5 + p_empate_f5:.1%}"),
            ("RL Visita +0.5", f"{p_visita_f5 + p_empate_f5:.1%}"),
        ]
        for lbl, val in par_f5:
            estilo_celda(ws2, fila2, 1, lbl, font=bold_font)
            estilo_celda(ws2, fila2, 2, val)
            if fila2 % 2 == 0:
                ws2.cell(row=fila2, column=1).fill = fill_par
                ws2.cell(row=fila2, column=2).fill = fill_par
            fila2 += 1
        fila2 += 2

    # Anchos sheet 2
    for c in range(1, 11):
        ws2.column_dimensions[get_column_letter(c)].width = 18
    ws2.column_dimensions["A"].width = 22

    # ── SHEET 3: JUGADAS DE VALOR (+EV) ──
    ws3 = wb.create_sheet("Jugadas +EV")
    ws3.sheet_properties.tabColor = "00B050"

    ws3.merge_cells("A1:E1")
    ws3.cell(row=1, column=1, value="💰 JUGADAS CON VALOR ESPERADO POSITIVO").font = title_font
    ws3.merge_cells("A2:E2")
    ws3.cell(row=2, column=1, value="(Requiere ODDS_API_KEY activada en variable de entorno)").font = Font(name="Calibri", italic=True, color="999999", size=9)

    headers3 = ["Juego", "Mercado", "Pick", "Prob Modelo", "Momio", "EV", "Casa"]
    escribir_encabezados(ws3, 4, headers3)

    f3 = 5
    ods = m.valor.obtener_odds()
    for j in modelables:
        visita, casa = j["away_name"], j["home_name"]
        p_v, p_c = j["away_probable_pitcher"], j["home_probable_pitcher"]
        pv = m.datos_pitcher(p_v)
        pc = m.datos_pitcher(p_c)
        if pv is None or pc is None or pv["fip"] is None or pc["fip"] is None:
            continue
        fip_v = m.fip_blend(pv)
        fip_c = m.fip_blend(pc)
        ip_v, ip_c = pv["ip_esp"], pc["ip_esp"]
        rg_v = m.carreras_por_juego(visita, hoy)
        rg_c = m.carreras_por_juego(casa, hoy)
        park = m.PARK.get(casa, 1.00)
        split_v = m.split_ofensivo(visita, pc["mano"])
        split_c = m.split_ofensivo(casa, pv["mano"])
        bp_v = m.bullpen_stats(visita)
        bp_c = m.bullpen_stats(casa)
        def_v = m.factor_defensivo(visita)
        def_c = m.factor_defensivo(casa)
        pitcheo_c = m.fip_combinado(fip_c, ip_c, bp_c["fip"])
        pitcheo_v = m.fip_combinado(fip_v, ip_v, bp_v["fip"])
        lam_v = rg_v * split_v * m.multiplicador_pitcheo(pitcheo_c) * def_c * park * m.AJUSTE_BASE
        lam_c = rg_c * split_c * m.multiplicador_pitcheo(pitcheo_v) * def_v * park * m.AJUSTE_BASE * m.HFA
        overs, p_casa, p_casa_rl = m.simular(lam_v, lam_c)

        jugadas = m.valor.analizar_juego(m.valor.buscar(ods, visita, casa), visita, casa, p_casa, overs)
        for jg in jugadas:
            estilo_celda(ws3, f3, 1, f"{visita} @ {casa}", font=data_font)
            estilo_celda(ws3, f3, 2, jg["mercado"], font=data_font)
            estilo_celda(ws3, f3, 3, jg["pick"], font=bold_font)
            estilo_celda(ws3, f3, 4, round(jg["p_modelo"], 4), fmt=pct_fmt, font=data_font)
            mom = jg["momio"]
            estilo_celda(ws3, f3, 5, f"{mom:+d}" if mom > 0 else str(mom), font=data_font)
            estilo_celda(ws3, f3, 6, round(jg["ev"], 4), fmt=pct_fmt,
                       fill=PatternFill("solid", fgColor=VERDE))
            estilo_celda(ws3, f3, 7, jg.get("libro", ""), font=data_font)
            f3 += 1

    if f3 == 5:
        ws3.merge_cells(f"A3:F3")
        ws3.cell(row=3, column=1, value="⚠ Sin ODDS_API_KEY — no hay datos de mercado para detectar valor").font = Font(name="Calibri", italic=True, color="FF0000", size=10)

    for c in range(1, 8):
        ws3.column_dimensions[get_column_letter(c)].width = 20

    # ── Guardar ──
    os.makedirs("exports", exist_ok=True)
    filename = f"exports/MLB_Predicciones_{hoy.replace('/', '-')}.xlsx"
    wb.save(filename)
    print(f"\n✅ Excel generado: {os.path.abspath(filename)}", flush=True)
    print(f"   {len(modelables)} juegos modelados", flush=True)
    print(f"   Abrelo en Excel para ver formato completo con colores", flush=True)

if __name__ == "__main__":
    fecha = sys.argv[1] if len(sys.argv) > 1 else None
    generar(fecha)
