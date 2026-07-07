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
    visita, casa = j["away_name"], j["home_name"]
    p_v, p_c = j["away_probable_pitcher"], j["home_probable_pitcher"]

    pv = m.datos_pitcher(p_v)
    pc = m.datos_pitcher(p_c)
    if pv is None or pc is None or pv["fip"] is None or pc["fip"] is None:
        return None

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
    rl_casa_f5 = p_casa_f5 + p_empate_f5
    rl_visita_f5 = p_visita_f5 + p_empate_f5

    jugadas = v.analizar_juego(v.buscar(odds_slate, visita, casa), visita, casa, p_casa, overs)

    bateo = m.predecir_hits_juego(visita, casa, j.get("game_id"), pv, pc, park, split_v, split_c)

    juego_dict = {
        "visita": visita,
        "casa": casa,
        "abridor_v": p_v,
        "abridor_c": p_c,
        "fip_v": round(pv["fip"], 2) if pv["fip"] is not None else None,
        "fip_c": round(pc["fip"], 2) if pc["fip"] is not None else None,
        "fip_v_reciente": round(pv["fip_reciente"], 2) if pv.get("fip_reciente") else None,
        "fip_c_reciente": round(pc["fip_reciente"], 2) if pc.get("fip_reciente") else None,
        "ip_v": round(ip_v, 1) if ip_v is not None else None,
        "ip_c": round(ip_c, 1) if ip_c is not None else None,
        "mano_v": mano_v,
        "mano_c": mano_c,
        "k9_v": round(pv["k9"], 1) if pv["k9"] else None,
        "k9_c": round(pc["k9"], 1) if pc["k9"] else None,
        "bb9_v": round(pv["bb9"], 1) if pv["bb9"] else None,
        "bb9_c": round(pc["bb9"], 1) if pc["bb9"] else None,
        "bullpen_v": round(bp_v["fip"], 2),
        "bullpen_c": round(bp_c["fip"], 2),
        "bp_k9_v": round(bp_v["k9"], 1),
        "bp_k9_c": round(bp_c["k9"], 1),
        "rg_v": round(rg_v, 2),
        "rg_c": round(rg_c, 2),
        "split_v": round(split_v, 3),
        "split_c": round(split_c, 3),
        "park": park,
        "def_c": round(def_c, 3),
        "def_v": round(def_v, 3),
        "lam_v": round(lam_v, 2),
        "lam_c": round(lam_c, 2),
        "p_casa": round(p_casa, 4),
        "p_visita": round(1 - p_casa, 4),
        "p_casa_rl": round(p_casa_rl, 4),
        "p_visita_rl": round(1 - p_casa_rl, 4),
        "overs": {str(k): round(val, 4) for k, val in overs.items()},
        "f5": {
            "lam_v": round(lam_v_f5, 2),
            "lam_c": round(lam_c_f5, 2),
            "p_casa": round(p_casa_f5, 4),
            "p_visita": round(p_visita_f5, 4),
            "p_empate": round(p_empate_f5, 4),
            "rl_casa": round(rl_casa_f5, 4),
            "rl_visita": round(rl_visita_f5, 4),
            "overs": {str(k): round(val, 4) for k, val in overs_f5.items()},
        },
        "jugadas_valor": [
            {
                "mercado": jg["mercado"],
                "pick": jg["pick"],
                "linea": jg.get("linea", ""),
                "p_modelo": round(jg["p_modelo"], 4),
                "p_mercado": round(jg["p_mercado"], 4),
                "momio": jg["momio"],
                "ev": round(jg["ev"], 4),
                "libro": jg.get("libro", ""),
            }
            for jg in jugadas
        ],
        "bateo": bateo,
    }
    
    return juego_dict, lam_v + lam_c, len(jugadas)

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
