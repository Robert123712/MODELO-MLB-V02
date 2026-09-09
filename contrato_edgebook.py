# ============================================================
# CONTRATO EDGEBOOK — traduce la salida del modelo a lo que Edgebook consume
#
# El modelo piensa en beisbol: lambdas, FIP, run line, NRFI. Edgebook solo
# necesita cuatro cosas por partido: quien juega, el marcador proyectado, la
# probabilidad y el pick. La traduccion vive aqui para que Edgebook no tenga que
# saber nada de beisbol.
#
# Deliberadamente NO lleva:
#   - Precios. El modelo no consume momios: no hay edge, cuota justa ni +EV.
#     Edgebook muestra el momio de ESPN aparte y solo como referencia visual.
#   - Grades ni notas. El pick se muestra tal cual; no se califica.
#   - Mercados secundarios. Run line, totales, F5 y NRFI siguen en el snapshot
#     interno (docs/data/latest.json) para la pagina del modelo; aqui no.
#
# Publica docs/data/edgebook-latest.json junto al snapshot de siempre. Archivos
# separados a proposito: la pagina de GitHub Pages y Edgebook son consumidores
# distintos y ninguno deberia romperse cuando el otro necesite un campo nuevo.
# ============================================================

from functools import lru_cache

SCHEMA_VERSION = "1.0"
SPORT = "MLB"


@lru_cache(maxsize=1)
def _codigos():
    """{nombre de equipo: abreviatura}. Una sola llamada por corrida.

    Edgebook cruza contra ESPN, que usa abreviaturas. Mandar el codigo evita que
    el cruce dependa de que dos proveedores escriban el nombre igual.
    """
    import statsapi
    try:
        equipos = statsapi.get("teams", {"sportId": 1})["teams"]
        return {e["name"]: e.get("abbreviation") for e in equipos}
    except Exception:
        return {}


def _equipo(nombre):
    return {"name": nombre, "code": _codigos().get(nombre)}


def _aviso(juego):
    """Por que esta proyeccion puede valer menos. None si no hay nada que avisar.

    Es un aviso, no un filtro: el partido se muestra igual. Esconderlo obligaria
    al usuario a preguntarse por que falta.
    """
    if juego.get("estimado_v") or juego.get("estimado_c"):
        return "Abridor estimado"
    if not juego.get("hay_lineup"):
        return "Sin alineación"
    return None


def proyeccion(juego):
    """Traduce un juego del snapshot interno al formato que consume Edgebook."""
    p_casa, p_visita = juego["p_casa"], juego["p_visita"]
    gana_casa = p_casa >= p_visita
    equipo = juego["casa"] if gana_casa else juego["visita"]
    codigo = _codigos().get(equipo) or equipo

    return {
        "league_game_id": str(juego["game_id"]) if juego.get("game_id") else None,
        "starts_at": juego.get("game_datetime"),
        "home": _equipo(juego["casa"]),
        "away": _equipo(juego["visita"]),

        # La pieza visual principal: un marcador se lee sin explicacion, una
        # lambda no. Sale de lam_c/lam_v del modelo; la interfaz no lo inventa.
        "projection": {"home_score": juego["lam_c"], "away_score": juego["lam_v"]},
        "win_probability": {"home": p_casa, "away": p_visita},
        "pick": {
            "market": "moneyline",
            "side": "home" if gana_casa else "away",
            "label": f"{codigo} ML",
        },
        "data_warning": _aviso(juego),
    }


def envelope(snapshot):
    """Envuelve un snapshot completo (la salida de _ejecutar_simulacion).

    Los juegos sin game_id se OMITEN: sin identidad dura Edgebook no puede
    cruzarlos con su slate, y adivinar por nombre falla justo en las dobles
    carteleras. El conteo va en el envelope para que la omision sea visible en
    vez de silenciosa.
    """
    juegos = snapshot.get("juegos") or []
    con_id = [j for j in juegos if j.get("game_id")]
    primero = juegos[0] if juegos else {}

    mm, dd, yyyy = snapshot["fecha"].split("/")
    return {
        "schema_version": SCHEMA_VERSION,
        "sport": SPORT,
        # model_version y generado_en viven por juego en el snapshot interno;
        # son iguales en toda la corrida, asi que aqui suben al envelope.
        "model_version": primero.get("model_version"),
        "generated_at": snapshot.get("generado_en") or primero.get("generado_en"),
        "date_local": f"{yyyy}-{mm}-{dd}",
        "games": [proyeccion(j) for j in con_id],
        "skipped_without_id": len(juegos) - len(con_id),
    }
