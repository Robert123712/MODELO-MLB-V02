# ============================================================
# GENERADOR DE JSON — snapshot diario para la pagina web estatica
# Corre la simulacion del dia (reutiliza la logica de app.py) y escribe:
#   docs/data/latest.json          <- lo que lee la pagina de GitHub Pages
#   docs/data/YYYY-MM-DD.json      <- copia con fecha (historico)
# Uso:  python generar_json.py [mm/dd/YYYY]
# Lo ejecuta el GitHub Action de corridas automaticas (modelo-diario.yml).
# ============================================================

import json
import os
import sys
from datetime import date, datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app import SimularRequest, _ejecutar_simulacion


def generar(fecha=None):
    hoy = fecha or date.today().strftime("%m/%d/%Y")
    print(f"📡 Simulando slate del {hoy} para la página...", flush=True)

    data = _ejecutar_simulacion(SimularRequest(fecha=hoy))
    data["generado_en"] = datetime.now(timezone.utc).isoformat()

    os.makedirs("docs/data", exist_ok=True)

    mm, dd, yyyy = hoy.split("/")
    con_fecha = f"docs/data/{yyyy}-{mm}-{dd}.json"
    for ruta in ("docs/data/latest.json", con_fecha):
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

    print(f"✅ {len(data['juegos'])} juegos → docs/data/latest.json y {con_fecha}", flush=True)


if __name__ == "__main__":
    generar(sys.argv[1] if len(sys.argv) > 1 else None)
