# ============================================================
# ¿LANZAR EN CASA ES UNA HABILIDAD DEL PITCHER?
#
# Que un pitcher rinda mejor en casa es un hecho de liga. La pregunta distinta
# —y la que decide si el modelo debe incluirlo POR PITCHER— es si cada uno
# tiene su propia tendencia que PERSISTE, o si lo que vemos es parque + azar.
#
# El experimento separa las tres capas:
#   1. Efecto de liga:   promedio de (FIP casa - FIP visita) de todos.
#   2. Efecto de parque: se elimina park-ajustando cada apertura.
#   3. Habilidad propia: prueba de mitades. Se parten las aperturas de cada
#      pitcher en pares/impares (evita tendencias de temporada), se calcula su
#      diferencial casa-visita en cada mitad y se correlacionan.
#         correlacion ~0  -> ruido: no hay habilidad que modelar
#         correlacion >0  -> persiste: vale la pena meterlo al modelo
#
# Uso:  python analisis_casa_visita.py
# Toma los abridores de predicciones.csv (los que el modelo ya evaluo).
# ============================================================

import csv
import math
import os
import sys

import statsapi

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from modelo_diario import PARK, calcular_fip, ip_a_decimal

MIN_IP_APERTURA = 1.0     # una salida mas corta es ruido puro
MIN_APERTURAS = 10        # menos que esto no alcanza para partir en mitades
MIN_POR_LADO = 2          # aperturas minimas en casa y en visita por mitad


def aperturas_del_pitcher(nombre):
    """[(fip_park_ajustado, ip, es_local)] de cada apertura de la temporada."""
    try:
        res = statsapi.lookup_player(nombre)
        if not res:
            return []
        pid = res[0]["id"]
        data = statsapi.get("person", {"personId": pid,
                                       "hydrate": "stats(group=[pitching],type=[gameLog])"})
        splits = []
        for g in (data.get("people") or [{}])[0].get("stats", []):
            if (g.get("group", {}).get("displayName") == "pitching"
                    and g.get("type", {}).get("displayName") == "gameLog"):
                splits = g.get("splits", [])
                break
    except Exception:
        return []

    salidas = []
    for g in splits:
        st = g.get("stat", {})
        ip = ip_a_decimal(st.get("inningsPitched", 0))
        if ip < MIN_IP_APERTURA:
            continue
        fip = calcular_fip(st.get("homeRuns", 0) or 0, st.get("baseOnBalls", 0) or 0,
                           st.get("hitByPitch", 0) or 0, st.get("strikeOuts", 0) or 0, ip)
        if fip is None:
            continue
        local = bool(g.get("isHome"))
        propio = (g.get("team") or {}).get("name")
        rival = (g.get("opponent") or {}).get("name")
        sede = propio if local else rival
        # Se quita el parque: sin esto estariamos midiendo Coors, no al pitcher
        salidas.append((fip / PARK.get(sede, 1.00), ip, local))
    return salidas


def _fip_ponderado(salidas):
    ip = sum(s[1] for s in salidas)
    return (sum(s[0] * s[1] for s in salidas) / ip) if ip > 0 else None


def diferencial(salidas):
    """FIP en casa menos FIP de visita. Negativo = mejor en casa."""
    casa = [s for s in salidas if s[2]]
    visita = [s for s in salidas if not s[2]]
    if len(casa) < MIN_POR_LADO or len(visita) < MIN_POR_LADO:
        return None
    fc, fv = _fip_ponderado(casa), _fip_ponderado(visita)
    return None if fc is None or fv is None else fc - fv


def correlacion(pares):
    n = len(pares)
    if n < 3:
        return None
    mx = sum(p[0] for p in pares) / n
    my = sum(p[1] for p in pares) / n
    sxy = sum((x - mx) * (y - my) for x, y in pares)
    sxx = sum((x - mx) ** 2 for x, _ in pares)
    syy = sum((y - my) ** 2 for _, y in pares)
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else None


def main():
    if not os.path.exists("predicciones.csv"):
        print("No hay predicciones.csv del cual sacar la lista de abridores.")
        return 1
    with open("predicciones.csv", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    nombres = sorted({n for fila in filas for n in (fila.get("abridor_v"), fila.get("abridor_c")) if n})
    print(f"Abridores a analizar: {len(nombres)}\n")

    difs, mitades = [], []
    analizados = 0
    for i, nombre in enumerate(nombres, 1):
        if i % 25 == 0:
            print(f"  ...{i}/{len(nombres)}", flush=True)
        salidas = aperturas_del_pitcher(nombre)
        if len(salidas) < MIN_APERTURAS:
            continue
        d = diferencial(salidas)
        if d is None:
            continue
        analizados += 1
        difs.append(d)
        # mitades alternadas: neutraliza lesiones y tendencias de la temporada
        a = diferencial(salidas[0::2])
        b = diferencial(salidas[1::2])
        if a is not None and b is not None:
            mitades.append((a, b))

    if not difs:
        print("No hubo pitchers con muestra suficiente.")
        return 1

    n = len(difs)
    media = sum(difs) / n
    desv = math.sqrt(sum((d - media) ** 2 for d in difs) / n)

    print("\n" + "=" * 62)
    print("   ¿LANZAR EN CASA ES HABILIDAD DEL PITCHER O ES EL PARQUE?")
    print("=" * 62)
    print(f"\nPitchers con muestra suficiente: {analizados}")
    print(f"(park-ajustado, minimo {MIN_APERTURAS} aperturas y {MIN_POR_LADO} por lado)\n")

    print("1) EFECTO DE LIGA — promedio de (FIP casa - FIP visita)")
    print(f"     {media:+.3f} de FIP   ({'mejor' if media < 0 else 'peor'} en casa)")
    print("     Este SI existe y el modelo lo aplica via HFA repartido.\n")

    print("2) DISPERSION ENTRE PITCHERS")
    print(f"     desviacion estandar: {desv:.3f} de FIP")
    print(f"     rango observado: {min(difs):+.2f} a {max(difs):+.2f}")
    print("     Hay pitchers con diferencias enormes... pero eso solo no prueba")
    print("     habilidad: con pocas aperturas el azar produce dispersion igual.\n")

    print("3) LA PRUEBA QUE DECIDE — ¿persiste el diferencial?")
    r = correlacion(mitades)
    if r is None:
        print(f"     Muestra insuficiente ({len(mitades)} pitchers con dos mitades).")
    else:
        print(f"     Correlacion entre mitades: r = {r:+.3f}   (n = {len(mitades)})")
        print()
        if r < 0.10:
            print("     VEREDICTO: no persiste. Lo que un pitcher hizo en casa en la")
            print("     mitad A no predice lo que hara en la mitad B: es azar.")
            print("     Meterlo por pitcher seria agregar ruido con cara de senal.")
        elif r < 0.25:
            print("     VEREDICTO: senal debil. Podria valer con MUCHA regresion")
            print("     (encogerlo ~80% hacia cero), pero el efecto neto seria minimo.")
        else:
            print("     VEREDICTO: SI persiste. Vale la pena modelarlo por pitcher,")
            print("     con la regresion que corresponda al tamano de muestra.")
    print("\n" + "=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
