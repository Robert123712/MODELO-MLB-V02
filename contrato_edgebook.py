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
# Si lleva key_factors: el pick sin explicacion obliga al usuario a creer o no
# creer, sin nada en medio. Son deterministas y cada uno cita un numero del
# modelo, para que la descripcion se pueda comprobar.
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


def _factores(juego, lado_pick):
    """Hasta tres factores que explican la proyeccion, en orden de peso.

    Cada uno cita un numero que el modelo calculo, para que la descripcion del
    pick se pueda comprobar en vez de tener que creerla. La tarjeta los redacta;
    no los inventa.

    Se ordenan poniendo primero los que APOYAN el pick, porque leer "el abridor
    visitante llega mejor" debajo de un pick por la casa se lee como una
    contradiccion. Los contrarios no se esconden: van despues, y la tarjeta los
    marca. Que el modelo elija un lado a pesar de un factor en contra es
    informacion, no algo que ocultar.
    """
    def lado(diferencia):
        """Positivo favorece a la casa."""
        return "home" if diferencia > 0 else "away"

    def orden(mejor_es_casa, casa, visita):
        """(mejor, peor) segun de que lado este la ventaja."""
        return (casa, visita) if mejor_es_casa else (visita, casa)

    codigos = _codigos()
    cv = codigos.get(juego["visita"]) or juego["visita"]
    cc = codigos.get(juego["casa"]) or juego["casa"]

    candidatos = []

    # FIP reciente del abridor: mas bajo es mejor, asi que la resta se invierte.
    fip_v, fip_c = juego.get("fip_v_reciente"), juego.get("fip_c_reciente")
    if fip_v is not None and fip_c is not None and abs(fip_v - fip_c) >= 0.75:
        mejor, peor = orden(fip_c < fip_v,
                            (juego["abridor_c"], fip_c),
                            (juego["abridor_v"], fip_v))
        candidatos.append((abs(fip_v - fip_c), {
            "label": "Abridor",
            "favors": lado(fip_v - fip_c),
            "detail": f"{mejor[0]} llega con {mejor[1]:.2f} de FIP reciente; "
                      f"{peor[0]} con {peor[1]:.2f}",
        }))

    bp_v, bp_c = juego.get("bullpen_v"), juego.get("bullpen_c")
    if bp_v is not None and bp_c is not None and abs(bp_v - bp_c) >= 0.40:
        mejor, peor = orden(bp_c < bp_v, (cc, bp_c), (cv, bp_v))
        candidatos.append((abs(bp_v - bp_c), {
            "label": "Bullpen",
            "favors": lado(bp_v - bp_c),
            "detail": f"el bullpen de {mejor[0]} tiene {mejor[1]:.2f} de FIP "
                      f"contra {peor[1]:.2f} el de {peor[0]}",
        }))

    of_v = (juego.get("rg_v") or 0) * (juego.get("split_v") or 1)
    of_c = (juego.get("rg_c") or 0) * (juego.get("split_c") or 1)
    if abs(of_c - of_v) >= 0.50:
        mejor, peor = orden(of_c > of_v, (cc, of_c), (cv, of_v))
        candidatos.append((abs(of_c - of_v), {
            "label": "Ofensiva",
            "favors": lado(of_c - of_v),
            "detail": f"{mejor[0]} produce {mejor[1]:.2f} carreras por juego "
                      f"ajustadas contra {peor[1]:.2f} de {peor[0]}",
        }))

    park = juego.get("park")
    if park is not None and abs(park - 1) >= 0.05:
        candidatos.append((abs(park - 1) * 10, {
            "label": "Parque",
            "favors": None,
            "detail": f"el parque {'favorece' if park > 1 else 'suprime'} la anotación "
                      f"(factor {park:.2f})",
        }))

    def prioridad(entrada):
        peso, factor = entrada
        apoya = 0 if factor["favors"] == lado_pick else 1 if factor["favors"] is None else 2
        return (apoya, -peso)

    candidatos.sort(key=prioridad)
    return [
        {**factor, "supports_pick": factor["favors"] == lado_pick}
        for _, factor in candidatos[:3]
    ]


def proyeccion(juego):
    """Traduce un juego del snapshot interno al formato que consume Edgebook."""
    p_casa, p_visita = juego["p_casa"], juego["p_visita"]
    gana_casa = p_casa >= p_visita
    lado_pick = "home" if gana_casa else "away"
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
        # Por que el modelo eligio ese lado. Deterministas y trazables a un
        # numero del modelo: la tarjeta los redacta, no los inventa.
        "key_factors": _factores(juego, lado_pick),
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
