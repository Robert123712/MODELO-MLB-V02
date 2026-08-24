---
name: modelo-mlb
description: Contexto y reglas del simulador Monte Carlo de MLB — arquitectura, tubería de cálculo, parámetros de calibración y los errores estadísticos en los que este proyecto ya cayó. Usar al tocar el modelo, agregar mercados, cambiar la calibración, trabajar la página web o el flujo de corridas automáticas.
---

# Modelo MLB — contexto del proyecto

Simulador Monte Carlo que estima probabilidades de apuestas de béisbol y las
compara contra el mercado. Corre solo todos los días vía GitHub Actions y
publica en GitHub Pages.

## Regla de oro

**Toda la matemática vive en `modelo_diario.evaluar_juego()`.** Es la fuente
única de verdad: `app.py`, `generar_excel.py`, `analisis_comparacion.py` y
`correr()` solo dan formato a lo que esa función devuelve. Si un cambio de
modelo te obliga a editar dos archivos, lo estás haciendo mal — la fórmula
estuvo copiada 6 veces y eso ya causó un bug de variables cruzadas entre juegos.

## Arquitectura

| Archivo | Rol |
|---|---|
| `modelo_diario.py` | Motor: datos, `evaluar_juego()`, simulación, salida a consola y CSV |
| `valor.py` | Detección +EV contra The Odds API (de-vig, línea de consenso) |
| `app.py` | API FastAPI local; serializa `evaluar_juego()` a JSON |
| `templates/index.html` | Dashboard interactivo (servido por `app.py`) |
| `docs/index.html` | **Generado desde el template**; carga `docs/data/latest.json` para GitHub Pages |
| `generar_json.py` | Escribe el snapshot que consume la página pública |
| `generar_excel.py` | Exporta el slate a Excel (3 hojas) |
| `validar.py` | Calibración del histórico: Brier, log-loss, curva, MAE/bias |
| `analisis_comparacion.py` | Backtest juego por juego de una fecha |
| `tracker.py` | Diario de apuestas: registro, calificación, ROI (`apuestas.csv` NO se versiona) |

`docs/index.html` y `templates/index.html` comparten CSS y funciones de render;
solo difieren en el arranque (fetch del snapshot vs POST a la API). Al cambiar
la interfaz, edita el template y regenera `docs/` — si no, se desincronizan.

## La tubería de cálculo

```
ambiente = park × clima
λ_visita = rg_v × split_v × lineup_v × mult_pitcheo(casa)   × def_casa   × ambiente × base ÷ HFA
λ_casa   = rg_c × split_c × lineup_c × mult_pitcheo(visita) × def_visita × ambiente × base × HFA
```

El cruce es intencional: **el pitcheo y la defensa de un equipo deprimen la
ofensiva del otro**. El **HFA va repartido** (× de un lado, ÷ del otro): así
mueve el moneyline sin tocar el total del juego.

El pitcheo que entra en `mult_pitcheo` no es el FIP plano: se pondera por
**vuelta al orden** (el abridor empeora cada vez que ve al lineup) y el bullpen
se degrada por **fatiga** según lo que tiró en los últimos 2 días.

De ahí, 50,000 simulaciones con **binomial negativa** (no Poisson: las carreras
de béisbol tienen varianza > media; una Poisson subestima los blowouts) generan
ML, run line, totales, totales por equipo, marcadores y la distribución. F5 y
NRFI usan la misma λ reescalada con su propia dispersión.

## Parámetros de calibración (las perillas)

| Constante | Valor | Qué hace |
|---|---|---|
| `AMORTIGUA` | 0.6 | Cuánto mueve el pitcheo la λ. Menor = más regresión a la media |
| `DISPERSION_K` | 4.0 | Dispersión de la binomial negativa. Menor = más varianza |
| `DISPERSION_K_F5` | 2.4 | Igual para F5 (menos entradas ⇒ más varianza relativa) |
| `DISPERSION_K_INN` | 0.38 | Por entrada, calibrada a ~52% NRFI de liga |
| `AJUSTE_BASE` | 0.94 | Nivel global; se ajusta para promediar ~8.5 carreras/slate |
| `PESO_OFENSIVA_RECIENTE` | 0.45 | Mezcla reciencia/temporada de la ofensiva |
| `SHRINK_IP` | 60 | IP de regresión del FIP hacia la liga |
| `FIP_REEMPLAZO` | 4.80 | Abridor sin stats de temporada |
| `HFA` | 1.045 | Ventaja de local repartida. **Calibrado con la validación** |
| `TTO_AJUSTE` | (-0.28, 0, .32, .55) | Penalización por vuelta al orden |
| `TEMP_PESO` | 0.004 | Carreras por °F sobre 70 |
| `FATIGA_PESO` | 0.030 | Degradación del bullpen por entrada de exceso |
| `LINEUP_TOPE` | 0.10 | Tope del ajuste por alineación del día |

**Nunca muevas una perilla "a ojo".** El camino correcto es correr `validar.py`
sobre el histórico, ver el sesgo, y mover. `validar.py` ya sugiere ajustar
`AJUSTE_BASE` cuando detecta bias consistente en totales.

## Cómo se comportan las líneas de casino

El modelo produce **probabilidades**; el mercado produce **precios**, y no son lo
mismo. Tres reglas implementadas en `prob_a_momio()` y `momio_mercado()`:

1. **`-100` no existe.** En momio americano el negativo dice cuánto arriesgas
   para ganar 100, así que −100 es idéntico a +100. Se rotula **EVEN** (o PK).
2. **La casa nunca publica el precio justo.** Le carga su margen (vig): las
   probabilidades implicadas de los dos lados suman ~104-105%. Por eso un juego
   50/50 se postea **−110/−110**, no EVEN/EVEN. Y el vig **se carga sobre el no
   favorito**: a 75% real el mercado pone −300/+250, no −360/+230 (repartirlo
   proporcionalmente exagera a los favoritos grandes).
3. **Los momios van en escalones**: 5 cerca del pick, 10 en favoritos medianos,
   25 cuando el precio se dispara. Nadie postea −107 en un moneyline.

**Consecuencia práctica:** el momio justo del modelo casi siempre se ve "mejor"
que el del casino — eso NO es valor, es el vig. Para juzgar valor hay que
comparar contra `momio_mercado()` (la línea que una casa realmente pondría) o
de-viguear la del casino, que es lo que hace `valor.py`.

La conversión vive **solo en `modelo_diario.py`**; `generar_excel.py` la importa.
El JS de las páginas tiene un espejo (`momio`/`momioCasa`) porque es formato de
presentación: si cambias las reglas en Python, cámbialas también ahí.

## Lo que la validación ya encontró (y por qué se mide antes de tocar)

Primera corrida de `validar.py` sobre 552 juegos:

- **El moneyline estaba peor que un volado** (Brier 0.252 > 0.250). Causa: el
  modelo daba 51.9% de victoria local cuando la realidad fue 54.5%. El `HFA`
  estaba subvalorado *y* mal modelado (solo inflaba la ofensiva local). Se
  repartió a dos lados y se calibró a ~54%.
- **Los totales de F5 salían 0.32 carreras bajos** de forma consistente. Causa:
  `f5_frac_liga` incluía juegos de extra innings, cuyas carreras extra inflaban
  el denominador. Ahora solo cuenta juegos de exactamente 9 entradas.
- Los totales del juego completo estaban **bien** (bias -0.08): no se tocó
  `AJUSTE_BASE`.

**Moraleja:** ningún parámetro se mueve por intuición. Se corre `validar.py`
(workflow "Validar calibracion"), se lee el sesgo, y se ajusta.

## Errores estadísticos en los que este proyecto ya cayó

Sirven como lista de verificación antes de agregar cualquier factor:

1. **Doble conteo.** Hubo un `factor_kbb` multiplicando la λ cuando el FIP *ya*
   contiene K y BB en su fórmula, y un `factor_calibracion` que reaplicaba la
   ponderación por reciencia que `carreras_por_juego` ya hacía. Ambos eliminados.
   **Antes de agregar un factor, pregunta: ¿esta señal ya entra por otro lado?**
2. **Muestras chicas sin encogimiento.** El FIP de 3 salidas (~15 IP) es casi
   ruido. Todo estimador debe regresar hacia la media según su tamaño de muestra.
3. **Contexto sin ajustar.** Las carreras crudas contaban Coors como talento
   ofensivo. `carreras_por_juego` ahora divide entre el park de la sede.
4. **Perseguir rachas.** La reciencia pura hacía que el ML lo decidiera quién
   bateó bien esa semana, peleando contra el mercado por ruido propio.
5. **Centrar los ajustes nuevos.** El FIP de temporada ya promedia todas las
   vueltas al orden; aplicar el TTO crudo habría movido el nivel global de
   carreras y roto la calibración. Se centra en una apertura de referencia para
   que el efecto sea puramente relativo. **Todo factor nuevo debe ser neutral en
   promedio**, o hay que recalibrar `AJUSTE_BASE`.
6. **Fallos silenciosos.** Un `except: pass` escondió durante semanas que los 30
   bullpens tenían FIP idéntico (la API no expone `fip`; hay que calcularlo).
   **Los fallos de datos deben avisar en consola, no tragarse.**

## Convenciones

- Código y comentarios **en español, sin acentos en el código** (la consola de
  Windows del autor truena); los textos de la interfaz sí llevan acentos.
- Nombres de variables del dominio: `lam_v`/`lam_c`, `rg`, `split`, `fip`, `p_casa`.
- Datos personales fuera del repo: `apuestas.csv` y `banca.csv` están en `.gitignore`.
- Caches en memoria por proceso (`cache_pitcher`, `cache_equipo`…); el RNG es
  **por hilo** (`_rng()`) porque `app.py` simula en paralelo.
- Al agregar columnas a `predicciones.csv`, actualiza `CABECERA` en `correr()`:
  el código reescribe la cabecera vieja para no desalinear el histórico.

## Flujo de corridas automáticas

`.github/workflows/modelo-diario.yml` corre diario ~16:30 UTC: ejecuta el
modelo, acumula `predicciones.csv`, genera `docs/data/latest.json` y commitea a
`main`. Se dispara a mano desde Actions. El secret opcional `ODDS_API_KEY`
activa la detección +EV.

## Verificación

No hay suite de tests. Antes de dar por bueno un cambio del modelo:
1. `python -m py_compile *.py`
2. Prueba con casos sintéticos (mockea `cache_pitcher`/`cache_equipo` y llama
   `evaluar_juego`) — comprueba que las probabilidades sumen 1 y que un cambio
   de input mueva la salida en la dirección esperada.
3. `python validar.py` si el cambio afecta la calibración.
