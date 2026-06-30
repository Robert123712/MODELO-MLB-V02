# ============================================================
# TRACKER DE APUESTAS — diario personal + calificador automatico
#   - Registro interactivo de lo que apostaste (modelo o read propio)
#   - Califica contra el resultado REAL de MLB StatsAPI (incluye F5 por entrada)
#   - Reporte: record, ROI, desglose por mercado y curva de bankroll
# Uso:
#   python tracker.py            -> califica pendientes y muestra el reporte
#   python tracker.py log [fecha]-> registro interactivo (fecha mm/dd/YYYY)
# ============================================================

import csv
import os
import sys
from datetime import date

import statsapi

from valor import americano_a_decimal, _canon

# #6: consola de Windows en UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ARCHIVO = "apuestas.csv"
CAMPOS = ["fecha", "visita", "casa", "mercado", "pick", "linea",
          "momio", "stake", "estado", "resultado", "ganancia"]

MERCADOS = {
    "ML":   "Moneyline juego completo        (pick: casa/visita)",
    "RL":   "Run line +-1.5 juego completo   (pick: casa/visita, spread firmado)",
    "TOT":  "Total juego completo            (pick: over/under, requiere linea)",
    "ML5":  "Moneyline F5 a 3 vias           (pick: casa/empate/visita)",
    "RL5":  "Run line F5 +-0.5               (pick: casa/visita, spread firmado)",
    "TOT5": "Total F5                        (pick: over/under, requiere linea)",
}

# ---------------- CALIFICACION ----------------

def _resultado_juego(fecha, visita, casa):
    """Busca el juego en la fecha y devuelve (fh, fa, f5h, f5a) o None si no es Final
    aun, o 'NOJUEGO' si no lo encuentra. fh/fa = final; f5h/f5a = primeras 5."""
    try:
        sch = statsapi.schedule(date=fecha)
    except Exception:
        return None
    cv, cc = _canon(visita), _canon(casa)
    for g in sch:
        if _canon(g["away_name"]) != cv or _canon(g["home_name"]) != cc:
            continue
        if g["status"] != "Final":
            return None
        fh, fa = g["home_score"], g["away_score"]
        f5h = f5a = None
        try:
            ls = statsapi.get("game_linescore", {"gamePk": g["game_id"]})
            inn = ls.get("innings", [])
            f5h = sum((i.get("home", {}).get("runs", 0) or 0) for i in inn[:5])
            f5a = sum((i.get("away", {}).get("runs", 0) or 0) for i in inn[:5])
        except Exception:
            pass
        return fh, fa, f5h, f5a
    return "NOJUEGO"


def _calificar(mercado, pick, linea, fh, fa, f5h, f5a):
    """Devuelve 'GANADA' / 'PERDIDA' / 'PUSH'."""
    pick = (pick or "").strip().lower()

    if mercado == "ML":
        if fh == fa:
            return "PUSH"
        gana = "casa" if fh > fa else "visita"
        return "GANADA" if pick == gana else "PERDIDA"

    if mercado == "ML5":
        if f5h > f5a:
            gana = "casa"
        elif f5a > f5h:
            gana = "visita"
        else:
            gana = "empate"
        return "GANADA" if pick == gana else "PERDIDA"

    if mercado in ("RL", "RL5"):
        h, a = (fh, fa) if mercado == "RL" else (f5h, f5a)
        margen = (h - a) if pick == "casa" else (a - h)
        res = margen + float(linea)              # linea = spread firmado del lado elegido
        return "GANADA" if res > 0 else ("PUSH" if res == 0 else "PERDIDA")

    if mercado in ("TOT", "TOT5"):
        h, a = (fh, fa) if mercado == "TOT" else (f5h, f5a)
        total = h + a
        L = float(linea)
        if total == L:
            return "PUSH"
        return "GANADA" if ((total > L) == (pick == "over")) else "PERDIDA"

    return "PERDIDA"


def _ganancia(resultado, momio, stake):
    """Ganancia NETA en unidades para esa apuesta."""
    stake = float(stake)
    if resultado == "PUSH":
        return 0.0
    if resultado == "PERDIDA":
        return -stake
    return stake * (americano_a_decimal(momio) - 1)


def _requiere_f5(mercado):
    return mercado in ("ML5", "RL5", "TOT5")


def calificar_pendientes():
    """Recorre apuestas.csv, califica las que ya terminaron y reescribe el archivo."""
    if not os.path.exists(ARCHIVO):
        print(f"No existe {ARCHIVO} todavia. Registra apuestas con: python tracker.py log")
        return []
    with open(ARCHIVO, encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))

    cache_juego = {}
    nuevas = 0
    for fila in filas:
        if fila.get("estado") == "calificada":
            continue
        clave = (fila["fecha"], fila["visita"], fila["casa"])
        if clave not in cache_juego:
            cache_juego[clave] = _resultado_juego(*clave)
        res = cache_juego[clave]
        if res is None:
            continue                              # aun no es Final
        if res == "NOJUEGO":
            print(f"⚠ No encontre el juego {fila['visita']} @ {fila['casa']} ({fila['fecha']})")
            continue
        fh, fa, f5h, f5a = res
        if _requiere_f5(fila["mercado"]) and (f5h is None or f5a is None):
            print(f"⚠ Sin marcador por entrada para F5: {fila['visita']} @ {fila['casa']}")
            continue
        resultado = _calificar(fila["mercado"], fila["pick"], fila.get("linea") or 0,
                               fh, fa, f5h, f5a)
        fila["resultado"] = resultado
        fila["ganancia"] = f"{_ganancia(resultado, fila['momio'], fila['stake']):.3f}"
        fila["estado"] = "calificada"
        nuevas += 1

    with open(ARCHIVO, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows(filas)

    if nuevas:
        print(f"✅ {nuevas} apuestas nuevas calificadas.\n")
    return filas

# ---------------- REPORTE ----------------

def _fmt_u(x):
    return f"{x:+.2f}u"


def reporte(filas):
    cal = [f for f in filas if f.get("estado") == "calificada"]
    pend = [f for f in filas if f.get("estado") != "calificada"]
    print("=" * 48)
    print("           TRACKER DE APUESTAS")
    print("=" * 48)
    print(f"Calificadas: {len(cal)} | Pendientes: {len(pend)}\n")
    if not cal:
        print("Aun no hay apuestas calificadas (¿los juegos ya terminaron?).")
        return

    def resumen(grupo):
        w = sum(1 for f in grupo if f["resultado"] == "GANADA")
        l = sum(1 for f in grupo if f["resultado"] == "PERDIDA")
        p = sum(1 for f in grupo if f["resultado"] == "PUSH")
        stake = sum(float(f["stake"]) for f in grupo)
        neto = sum(float(f["ganancia"]) for f in grupo)
        roi = neto / stake if stake else 0.0
        decididas = w + l
        acierto = w / decididas if decididas else 0.0
        return w, l, p, stake, neto, roi, acierto

    w, l, p, stake, neto, roi, acierto = resumen(cal)
    print("GLOBAL")
    print(f"  Record: {w}-{l}" + (f"-{p} (push)" if p else ""))
    print(f"  Stake total: {stake:.2f}u | Neto: {_fmt_u(neto)} | ROI: {roi:+.1%}")
    print(f"  % acierto (sin push): {acierto:.1%}\n")

    print("POR MERCADO")
    for mkt in MERCADOS:
        grupo = [f for f in cal if f["mercado"] == mkt]
        if not grupo:
            continue
        w, l, p, stake, neto, roi, _ = resumen(grupo)
        rec = f"{w}-{l}" + (f"-{p}" if p else "")
        print(f"  {mkt:5} | {len(grupo):2} ap | {rec:8} | {_fmt_u(neto):>8} | ROI {roi:+.1%}")

    print("\nBANKROLL (neto acumulado por fecha)")
    porfecha = {}
    for f in cal:
        porfecha.setdefault(f["fecha"], 0.0)
        porfecha[f["fecha"]] += float(f["ganancia"])
    acum = 0.0
    for fch in sorted(porfecha, key=lambda d: (d[6:10], d[0:2], d[3:5])):
        acum += porfecha[fch]
        print(f"  {fch}  {porfecha[fch]:+6.2f}u   (acum {acum:+.2f}u)")

# ---------------- REGISTRO INTERACTIVO ----------------

def _pedir(msg, opciones=None, permitir_vacio=False):
    while True:
        val = input(msg).strip()
        if not val and permitir_vacio:
            return ""
        if opciones and val.lower() not in opciones:
            print(f"   Opciones validas: {', '.join(opciones)}")
            continue
        if val or permitir_vacio:
            return val


def registrar(fecha=None, juegos=None):
    """Registro interactivo. Si stdin no es interactivo, sale sin molestar."""
    if not sys.stdin.isatty():
        return
    fecha = fecha or date.today().strftime("%m/%d/%Y")
    if juegos is None:
        try:
            juegos = statsapi.schedule(date=fecha)
        except Exception:
            print("No pude bajar el calendario.")
            return

    if input(f"\n¿Registrar apuestas de {fecha}? (Enter para saltar / s para registrar): ").strip().lower() not in ("s", "si", "y"):
        return

    print("\nJuegos del dia:")
    for i, g in enumerate(juegos, 1):
        print(f"  {i:2}) {g['away_name']} @ {g['home_name']}")

    nuevas = []
    while True:
        sel = input("\nNumero de juego (Enter para terminar): ").strip()
        if not sel:
            break
        try:
            g = juegos[int(sel) - 1]
        except (ValueError, IndexError):
            print("   Numero invalido."); continue
        visita, casa = g["away_name"], g["home_name"]

        print("   Mercados: " + " | ".join(MERCADOS))
        mercado = _pedir("   Mercado: ", opciones=[m.lower() for m in MERCADOS]).upper()

        if mercado in ("ML", "RL", "RL5"):
            pick = _pedir("   Pick (casa/visita): ", opciones=["casa", "visita"]).lower()
        elif mercado == "ML5":
            pick = _pedir("   Pick (casa/empate/visita): ", opciones=["casa", "empate", "visita"]).lower()
        else:  # TOT / TOT5
            pick = _pedir("   Pick (over/under): ", opciones=["over", "under"]).lower()

        linea = ""
        if mercado in ("RL", "RL5"):
            linea = _pedir(f"   Spread firmado del lado elegido (ej {'-1.5/+1.5' if mercado=='RL' else '-0.5/+0.5'}): ")
        elif mercado in ("TOT", "TOT5"):
            linea = _pedir("   Linea (ej 8.5): ")

        momio = _pedir("   Momio americano (ej +110 o -130): ")
        stake = _pedir("   Stake (unidades, ej 1): ")

        nuevas.append({
            "fecha": fecha, "visita": visita, "casa": casa,
            "mercado": mercado, "pick": pick, "linea": linea,
            "momio": momio, "stake": stake,
            "estado": "pendiente", "resultado": "", "ganancia": "",
        })
        print(f"   ✓ Registrada: {mercado} {pick} {linea} @ {momio}, {stake}u")

    if nuevas:
        nuevo = not os.path.exists(ARCHIVO)
        with open(ARCHIVO, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS)
            if nuevo:
                w.writeheader()
            w.writerows(nuevas)
        print(f"\n✅ {len(nuevas)} apuestas guardadas en {ARCHIVO}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "log":
        registrar(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        filas = calificar_pendientes()
        if filas:
            reporte(filas)
