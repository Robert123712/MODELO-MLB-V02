import os
import sys
from datetime import date
from concurrent.futures import ThreadPoolExecutor, TimeoutError

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

os.environ["PYTHONIOENCODING"] = "utf-8"

sys.stdout.reconfigure(encoding="utf-8")

import modelo_diario as m
import valor as v

# ---------- FastAPI ----------

app = FastAPI(title="MLB Modelo")

@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(os.path.dirname(__file__), "templates", "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())

class SimularRequest(BaseModel):
    fecha: str | None = None

class SimularResponse(BaseModel):
    fecha: str
    calibracion: dict
    hay_odds: bool
    juegos: list
    total_promedio_slate: float | None
    total_jugadas_valor: int

SIMULAR_TIMEOUT = 300

@app.post("/api/simular")
def api_simular(req: SimularRequest):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_ejecutar_simulacion, req)
        try:
            return future.result(timeout=SIMULAR_TIMEOUT)
        except TimeoutError:
            return JSONResponse(
                status_code=504,
                content={"error": "La simulación tardó demasiado. Reintenta o revisa conexión a las APIs de MLB."},
            )

def _procesar_un_juego(j, hoy, odds_slate, _frac_f5):
    """Evalua un juego y lo serializa a JSON para la web. Toda la matematica
    vive en modelo_diario.evaluar_juego(): aqui solo se da formato."""
    r = m.evaluar_juego(j, hoy, _frac_f5, con_bateo=True)
    if r is None:
        return None

    pv, pc, f5 = r["pv"], r["pc"], r["f5"]
    jugadas = v.analizar_juego(v.buscar(odds_slate, r["visita"], r["casa"]),
                               r["visita"], r["casa"], r["p_casa"], r["overs"])

    def _r(x, n=2):
        return round(x, n) if x is not None else None

    juego_dict = {
        "visita": r["visita"], "casa": r["casa"],
        "abridor_v": r["abridor_v"], "abridor_c": r["abridor_c"],
        "fip_v": _r(r["fip_v"]), "fip_c": _r(r["fip_c"]),
        "fip_v_reciente": _r(pv.get("fip_reciente")), "fip_c_reciente": _r(pc.get("fip_reciente")),
        "ip_v": _r(r["ip_v"], 1), "ip_c": _r(r["ip_c"], 1),
        "mano_v": r["mano_v"], "mano_c": r["mano_c"],
        "k9_v": _r(pv.get("k9"), 1), "k9_c": _r(pc.get("k9"), 1),
        "bb9_v": _r(pv.get("bb9"), 1), "bb9_c": _r(pc.get("bb9"), 1),
        "bullpen_v": _r(r["bp_v"]["fip"]), "bullpen_c": _r(r["bp_c"]["fip"]),
        "bp_k9_v": _r(r["bp_v"]["k9"], 1), "bp_k9_c": _r(r["bp_c"]["k9"], 1),
        "rg_v": _r(r["rg_v"]), "rg_c": _r(r["rg_c"]),
        "split_v": _r(r["split_v"], 3), "split_c": _r(r["split_c"], 3),
        "park": r["park"],
        "def_v": _r(r["def_v"], 3), "def_c": _r(r["def_c"], 3),
        "lam_v": _r(r["lam_v"]), "lam_c": _r(r["lam_c"]),
        "p_casa": _r(r["p_casa"], 4), "p_visita": _r(r["p_visita"], 4),
        "p_casa_rl": _r(r["p_casa_rl"], 4), "p_visita_rl": _r(r["p_visita_rl"], 4),
        "overs": {str(k): round(val, 4) for k, val in r["overs"].items()},
        "tt_visita": {str(k): round(val, 4) for k, val in r["tt_visita"].items()},
        "tt_casa": {str(k): round(val, 4) for k, val in r["tt_casa"].items()},
        "marcadores": [{"casa": mc["casa"], "visita": mc["visita"], "p": round(mc["p"], 4)}
                       for mc in r["marcadores"]],
        "dist_total": [round(float(x), 5) for x in r["dist_total"]],
        "nrfi": {k: round(val, 4) for k, val in r["nrfi"].items()},
        "estimado": r["estimado"],
        "estimado_v": r["estimado_v"], "estimado_c": r["estimado_c"],
        "f5": {
            "lam_v": _r(f5["lam_v"]), "lam_c": _r(f5["lam_c"]),
            "p_casa": _r(f5["p_casa"], 4), "p_visita": _r(f5["p_visita"], 4),
            "p_empate": _r(f5["p_empate"], 4),
            "rl_casa": _r(f5["rl_casa"], 4), "rl_visita": _r(f5["rl_visita"], 4),
            "overs": {str(k): round(val, 4) for k, val in f5["overs"].items()},
        },
        "jugadas_valor": [
            {"mercado": jg["mercado"], "pick": jg["pick"], "linea": jg.get("linea", ""),
             "p_modelo": round(jg["p_modelo"], 4), "p_mercado": round(jg["p_mercado"], 4),
             "momio": jg["momio"], "ev": round(jg["ev"], 4), "libro": jg.get("libro", "")}
            for jg in jugadas
        ],
        "bateo": r.get("bateo"),
    }
    return juego_dict, r["total_esp"], len(jugadas)

def _ejecutar_simulacion(req: SimularRequest):
    hoy = req.fecha or date.today().strftime("%m/%d/%Y")
    juegos = m.statsapi.schedule(date=hoy)
    modelables = [
        j for j in juegos
        if j["status"] in ("Scheduled", "Pre-Game", "Warmup")
        and j.get("away_probable_pitcher")
        and j.get("home_probable_pitcher")
    ]

    odds_slate = v.obtener_odds()
    _frac_f5 = m.f5_frac_liga(hoy)

    juegos_out = []
    totales_slate = []
    total_jugadas_valor = 0

    with ThreadPoolExecutor(max_workers=15) as pool:
        resultados = list(pool.map(lambda j: _procesar_un_juego(j, hoy, odds_slate, _frac_f5), modelables))

    for res in resultados:
        if res is not None:
            juego_dict, suma_lam, jugadas_count = res
            juegos_out.append(juego_dict)
            totales_slate.append(suma_lam)
            total_jugadas_valor += jugadas_count

    total_promedio = round(float(m.np.mean(totales_slate)), 2) if totales_slate else None

    return {
        "fecha": hoy,
        "calibracion": {
            "amortigua": m.AMORTIGUA,
            "dispersion_k": m.DISPERSION_K,
            "base": m.AJUSTE_BASE,
        },
        "hay_odds": bool(odds_slate),
        "juegos": juegos_out,
        "total_promedio_slate": total_promedio,
        "total_jugadas_valor": total_jugadas_valor,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
