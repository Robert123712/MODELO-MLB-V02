# ============================================================
# TRACKER DE APUESTAS — diario personal + calificador automatico
#   - Registro interactivo de lo que apostaste (modelo o read propio)
#   - Apuestas DERECHAS y PARLAYS (multi-pata, gana solo si TODAS pegan)
#   - Califica contra el resultado REAL de MLB StatsAPI (incluye F5 por entrada)
#   - Reporte: record, ROI, desglose por mercado y curva de bankroll
# Uso:
#   python tracker.py            -> califica pendientes y muestra el reporte
#   python tracker.py log [fecha]-> registro interactivo (fecha mm/dd/YYYY)
# ============================================================

import csv
import os
import sys
from datetime import date, datetime

import statsapi

from valor import americano_a_decimal, _canon

# #6: consola de Windows en UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ARCHIVO = "apuestas.csv"
# parlay_id vacio = apuesta derecha. Las patas de un parlay comparten parlay_id;
# el stake del parlay vive SOLO en su primera pata (las demas lo dejan vacio).
CAMPOS = ["fecha", "visita", "casa", "mercado", "pick", "linea",
          "momio", "stake", "parlay_id", "estado", "resultado", "ganancia"]

MERCADOS = {
    "ML":   "Moneyline juego completo        (pick: casa/visita)",
    "RL":   "Run line +-1.5 juego completo   (pick: casa/visita, spread firmado)",
    "TOT":  "Total juego completo            (pick: over/under, requiere linea)",
    "ML5":  "Moneyline F5 a 3 vias           (pick: casa/empate/visita)",
    "RL5":  "Run line F5 +-0.5               (pick: casa/visita, spread firmado)",
    "TOT5": "Total F5                        (pick: over/under, requiere linea)",
}

# ---------------- CALIFICACION DE UNA PATA ----------------

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
    """Devuelve 'GANADA' / 'PERDIDA' / 'PUSH' para una pata."""
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


def _ganancia_directa(resultado, momio, stake):
    """Ganancia NETA en unidades de una apuesta derecha (1 pata)."""
    stake = float(stake)
    if resultado == "PUSH":
        return 0.0
    if resultado == "PERDIDA":
        return -stake
    return stake * (americano_a_decimal(momio) - 1)


def _requiere_f5(mercado):
    return mercado in ("ML5", "RL5", "TOT5")


# ---------------- LIQUIDACION DE UNA UNIDAD (derecha o parlay) ----------------

def _stake_unidad(patas):
    """El stake vive en la pata que lo tenga (primera del parlay, o la unica)."""
    for p in patas:
        if (p.get("stake") or "").strip():
            return float(p["stake"])
    return 0.0


def _settle(patas):
    """Liquida una unidad ya calificada. Devuelve (resultado, ganancia_neta).
    Derecha = 1 pata. Parlay = N patas: gana solo si todas ganan; una pata en
    PUSH se cae y el parlay se recalcula con las que quedan."""
    stake = _stake_unidad(patas)

    if len(patas) == 1:
        r = patas[0]["resultado"]
        return r, _ganancia_directa(r, patas[0]["momio"], stake)

    # --- PARLAY ---
    if any(p["resultado"] == "PERDIDA" for p in patas):
        return "PERDIDA", -stake
    vivas = [p for p in patas if p["resultado"] == "GANADA"]
    if not vivas:                                  # todas hicieron push
        return "PUSH", 0.0
    decimal = 1.0
    for p in vivas:
        decimal *= americano_a_decimal(p["momio"])
    return "GANADA", stake * (decimal - 1)


def _agrupar(filas):
    """Agrupa filas en unidades de apuesta: cada derecha es 1 unidad; las patas
    con el mismo parlay_id forman 1 unidad."""
    sueltas, parlays = [], {}
    for f in filas:
        pid = (f.get("parlay_id") or "").strip()
        if pid:
            parlays.setdefault(pid, []).append(f)
        else:
            sueltas.append([f])
    return sueltas + list(parlays.values())


def _label(unidad):
    return unidad[0]["mercado"] if len(unidad) == 1 else f"PARLAY x{len(unidad)}"

# ---------------- LECTURA / ESCRITURA ----------------

def _leer():
    """Lee apuestas.csv y normaliza filas viejas (sin parlay_id) para compatibilidad."""
    if not os.path.exists(ARCHIVO):
        return None
    with open(ARCHIVO, encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    for fila in filas:
        for c in CAMPOS:
            fila.setdefault(c, "")
    return filas


def _guardar(filas):
    with open(ARCHIVO, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS, extrasaction="ignore")
        w.writeheader()
        w.writerows(filas)

# ---------------- CALIFICAR PENDIENTES ----------------

def calificar_pendientes():
    """Califica las unidades cuyos juegos ya terminaron y reescribe el archivo."""
    filas = _leer()
    if filas is None:
        print(f"No existe {ARCHIVO} todavia. Registra apuestas con: python tracker.py log")
        return []

    cache = {}

    def _res(p):
        clave = (p["fecha"], p["visita"], p["casa"])
        if clave not in cache:
            cache[clave] = _resultado_juego(*clave)
        return cache[clave]

    nuevas = 0
    for unidad in _agrupar(filas):
        if all(p.get("estado") == "calificada" for p in unidad):
            continue
        # un parlay no se liquida hasta que TODAS sus patas tengan resultado final
        resueltos = []
        listo = True
        for p in unidad:
            r = _res(p)
            if r is None:
                listo = False; break
            if r == "NOJUEGO":
                print(f"⚠ No encontre {p['visita']} @ {p['casa']} ({p['fecha']})")
                listo = False; break
            fh, fa, f5h, f5a = r
            if _requiere_f5(p["mercado"]) and (f5h is None or f5a is None):
                print(f"⚠ Sin marcador por entrada (F5): {p['visita']} @ {p['casa']}")
                listo = False; break
            resueltos.append((p, r))
        if not listo:
            continue

        for p, (fh, fa, f5h, f5a) in resueltos:
            p["resultado"] = _calificar(p["mercado"], p["pick"], p.get("linea") or 0,
                                        fh, fa, f5h, f5a)
            p["estado"] = "calificada"
        _, ganancia = _settle(unidad)
        # la ganancia de la unidad se guarda en la primera pata; las demas, vacia
        for i, p in enumerate(unidad):
            p["ganancia"] = f"{ganancia:.3f}" if i == 0 else ""
        nuevas += 1

    _guardar(filas)
    if nuevas:
        print(f"✅ {nuevas} apuestas nuevas calificadas.\n")
    return filas

# ---------------- REPORTE ----------------

def _fmt_u(x):
    return f"{x:+.2f}u"


def _resumen(unidades):
    w = l = p = 0
    stake = neto = 0.0
    for u in unidades:
        r, gan = _settle(u)
        stake += _stake_unidad(u)
        neto += gan
        if r == "GANADA":
            w += 1
        elif r == "PERDIDA":
            l += 1
        else:
            p += 1
    roi = neto / stake if stake else 0.0
    acierto = w / (w + l) if (w + l) else 0.0
    return w, l, p, stake, neto, roi, acierto


def reporte(filas):
    unidades = _agrupar(filas)
    cal = [u for u in unidades if all(p.get("estado") == "calificada" for p in u)]
    pend = [u for u in unidades if u not in cal]
    print("=" * 50)
    print("             TRACKER DE APUESTAS")
    print("=" * 50)
    print(f"Calificadas: {len(cal)} | Pendientes: {len(pend)}\n")
    if not cal:
        print("Aun no hay apuestas calificadas (¿los juegos ya terminaron?).")
        return

    w, l, p, stake, neto, roi, acierto = _resumen(cal)
    print("GLOBAL")
    print(f"  Record: {w}-{l}" + (f"-{p} (push)" if p else ""))
    print(f"  Stake total: {stake:.2f}u | Neto: {_fmt_u(neto)} | ROI: {roi:+.1%}")
    print(f"  % acierto (sin push): {acierto:.1%}\n")

    print("POR TIPO / MERCADO")
    etiquetas = list(MERCADOS) + ["PARLAY"]
    for et in etiquetas:
        if et == "PARLAY":
            grupo = [u for u in cal if len(u) > 1]
        else:
            grupo = [u for u in cal if len(u) == 1 and u[0]["mercado"] == et]
        if not grupo:
            continue
        w, l, p, stake, neto, roi, _ = _resumen(grupo)
        rec = f"{w}-{l}" + (f"-{p}" if p else "")
        print(f"  {et:7} | {len(grupo):2} ap | {rec:8} | {_fmt_u(neto):>8} | ROI {roi:+.1%}")

    # detalle de parlays
    parlays = [u for u in cal if len(u) > 1]
    if parlays:
        print("\nPARLAYS")
        for u in parlays:
            r, gan = _settle(u)
            patas = " + ".join(f"{p['mercado']}:{p['pick']}{(' '+p['linea']) if p['linea'] else ''}" for p in u)
            print(f"  [{r:7}] {_fmt_u(gan):>7} | {u[0]['fecha']} | {patas}")

    print("\nBANKROLL (neto acumulado por fecha)")
    porfecha = {}
    for u in cal:
        _, gan = _settle(u)
        porfecha.setdefault(u[0]["fecha"], 0.0)
        porfecha[u[0]["fecha"]] += gan
    acum = 0.0
    for fch in sorted(porfecha, key=lambda d: (d[6:10], d[0:2], d[3:5])):
        acum += porfecha[fch]
        print(f"  {fch}  {porfecha[fch]:+6.2f}u   (acum {acum:+.2f}u)")

# ---------------- REGISTRO INTERACTIVO ----------------

def _pedir(msg, opciones=None):
    while True:
        val = input(msg).strip()
        if opciones and val.lower() not in opciones:
            print(f"   Opciones validas: {', '.join(opciones)}")
            continue
        if val:
            return val


def _capturar_pata(juegos, fecha):
    """Captura UNA pata (juego + mercado + pick + linea + momio). None si cancela."""
    while True:
        sel = input("   Numero de juego (Enter para cancelar): ").strip()
        if not sel:
            return None
        try:
            g = juegos[int(sel) - 1]
            break
        except (ValueError, IndexError):
            print("   Numero invalido.")

    print("   Mercados: " + " | ".join(MERCADOS))
    mercado = _pedir("   Mercado: ", opciones=[m.lower() for m in MERCADOS]).upper()

    if mercado in ("ML", "RL", "RL5"):
        pick = _pedir("   Pick (casa/visita): ", opciones=["casa", "visita"]).lower()
    elif mercado == "ML5":
        pick = _pedir("   Pick (casa/empate/visita): ", opciones=["casa", "empate", "visita"]).lower()
    else:
        pick = _pedir("   Pick (over/under): ", opciones=["over", "under"]).lower()

    linea = ""
    if mercado in ("RL", "RL5"):
        linea = _pedir(f"   Spread firmado (ej {'-1.5/+1.5' if mercado=='RL' else '-0.5/+0.5'}): ")
    elif mercado in ("TOT", "TOT5"):
        linea = _pedir("   Linea (ej 8.5): ")

    momio = _pedir("   Momio americano de esta pata (ej +110 o -130): ")
    return {
        "fecha": fecha, "visita": g["away_name"], "casa": g["home_name"],
        "mercado": mercado, "pick": pick, "linea": linea, "momio": momio,
        "stake": "", "parlay_id": "", "estado": "pendiente",
        "resultado": "", "ganancia": "",
    }


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

    if input(f"\n¿Registrar apuestas de {fecha}? (Enter salta / s registra): ").strip().lower() not in ("s", "si", "y"):
        return

    print("\nJuegos del dia:")
    for i, g in enumerate(juegos, 1):
        print(f"  {i:2}) {g['away_name']} @ {g['home_name']}")

    nuevas = []
    while True:
        tipo = input("\nTipo (d=derecha / p=parlay / Enter=terminar): ").strip().lower()
        if not tipo:
            break

        if tipo == "p":
            print("  -- Arma el parlay: agrega patas; Enter en 'numero de juego' para cerrarlo --")
            patas = []
            while True:
                pata = _capturar_pata(juegos, fecha)
                if pata is None:
                    break
                patas.append(pata)
                print(f"   ✓ Pata {len(patas)}: {pata['mercado']} {pata['pick']} "
                      f"{pata['linea']} @ {pata['momio']}")
            if len(patas) < 2:
                print("   Un parlay necesita 2+ patas. Cancelado.")
                continue
            stake = _pedir(f"   Stake TOTAL del parlay ({len(patas)} patas, ej 1): ")
            pid = datetime.now().strftime("P%Y%m%d%H%M%S")
            for i, pata in enumerate(patas):
                pata["parlay_id"] = pid
                pata["stake"] = stake if i == 0 else ""    # stake solo en la 1a pata
            nuevas.extend(patas)
            print(f"   ✅ Parlay de {len(patas)} patas guardado (stake {stake}u)")

        elif tipo == "d":
            pata = _capturar_pata(juegos, fecha)
            if pata is None:
                continue
            pata["stake"] = _pedir("   Stake (unidades, ej 1): ")
            nuevas.append(pata)
            print(f"   ✓ Registrada: {pata['mercado']} {pata['pick']} "
                  f"{pata['linea']} @ {pata['momio']}, {pata['stake']}u")
        else:
            print("   Escribe 'd', 'p' o Enter.")

    if nuevas:
        nuevo = not os.path.exists(ARCHIVO)
        with open(ARCHIVO, "a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CAMPOS, extrasaction="ignore")
            if nuevo:
                w.writeheader()
            w.writerows(nuevas)
        print(f"\n✅ {len(nuevas)} filas guardadas en {ARCHIVO}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "log":
        registrar(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        filas = calificar_pendientes()
        if filas:
            reporte(filas)
