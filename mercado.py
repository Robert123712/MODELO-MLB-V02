# ============================================================
# LINEA DE CIERRE — la ultima observacion antes del primer pitcheo
#
# `precios.py` anota el precio varias veces al dia mientras el juego no empieza.
# Aqui esas observaciones se vuelven UNA por partido: la mas cercana al inicio,
# que es lo que en la practica se llama linea de cierre.
#
# La regla es fija y no se elige despues: siempre la ultima antes del primer
# pitcheo. Escoger cual observacion contar mirando el resultado seria quedarse
# con la que mas conviene, que es exactamente lo que este archivo evita.
#
# El cruce con las predicciones va por equipos y hora de inicio, no solo por
# equipos: en una doble cartelera el par apunta a dos juegos y la hora los
# separa. Sin hora que los distinga, no se cruza ninguno.
#
# Los momios NO entran al modelo. Se leen aqui y solo para comparar.
# ============================================================

import csv
import os
from datetime import datetime

import valor

ARCHIVO = "precios.csv"
# Una doble cartelera separa sus juegos por horas. Mas alla de esto ya no es el
# mismo partido, y cruzarlo seria inventar un precio.
TOLERANCIA_MINUTOS = 150


def cargar(ruta=ARCHIVO):
    if not os.path.exists(ruta):
        return []
    with open(ruta, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _hora(texto):
    if not texto:
        return None
    try:
        return datetime.fromisoformat(str(texto).replace("Z", "+00:00"))
    except ValueError:
        return None


def _numero(texto):
    try:
        return float(texto)
    except (TypeError, ValueError):
        return None


def indexar(filas):
    """Observaciones por par de equipos canonico."""
    indice = {}
    for fila in filas:
        clave = (valor._canon(fila.get("visita")), valor._canon(fila.get("casa")))
        indice.setdefault(clave, []).append(fila)
    return indice


def cierre(indice, visita, casa, comienza):
    """La observacion mas pegada al primer pitcheo de ESE juego.

    Devuelve None cuando no hay observaciones del partido, o cuando la hora no
    permite distinguir de que juego se trata: un precio del juego equivocado es
    peor que ninguno.
    """
    inicio = _hora(comienza)
    candidatos = indice.get((valor._canon(visita), valor._canon(casa)), [])
    if not candidatos or inicio is None:
        return None
    # Del mismo par de equipos puede haber dos juegos en el dia. Se toman solo
    # las observaciones cuyo inicio coincide con el del partido que se califica.
    del_juego = []
    for fila in candidatos:
        suyo = _hora(fila.get("comienza"))
        if suyo is None:
            continue
        if abs((suyo - inicio).total_seconds()) / 60 <= TOLERANCIA_MINUTOS:
            del_juego.append(fila)
    if not del_juego:
        return None
    # Regla fija: la ultima antes del primer pitcheo.
    return min(del_juego, key=lambda f: _numero(f.get("minutos_antes")) or 1e9)


def sin_vig(momio_visita, momio_casa):
    """Probabilidad de la casa con el margen repartido entre los dos lados.

    Sin quitarlo, las dos probabilidades suman mas de 1 y el mercado pareceria
    peor calibrado de lo que esta, que es una comparacion tramposa a su favor.

    La conversion y el de-vig salen de `valor`, que es donde ya vivian para la
    deteccion de valor. Tener dos formulas de de-vig en el repo pondria al
    modelo y a la referencia contra la que se mide en matematicas distintas, y
    la diferencia apareceria como si fuera merito o culpa del modelo.
    """
    v, c = _numero(momio_visita), _numero(momio_casa)
    if v is None or c is None:
        return None
    p_casa = valor.decimal_a_prob(valor.americano_a_decimal(c))
    p_visita = valor.decimal_a_prob(valor.americano_a_decimal(v))
    sin_margen, _ = valor.devig_dos_vias(p_casa, p_visita)
    return sin_margen if (p_casa + p_visita) > 0 else None
