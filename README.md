# Simulador Monte Carlo de MLB

Modelo predictivo de resultados de béisbol (MLB) construido en Python. Estima probabilidades de moneyline, run line y totales mediante simulación Monte Carlo, calibrada con estadísticas reales de la temporada.

## Qué hace

Para cada juego no iniciado del día, el modelo:

1. Obtiene los abridores anunciados vía la API oficial de MLB Stats
2. Calcula el FIP de cada abridor y estima sus innings esperados (promedio de últimas 3 salidas)
3. Deriva la ofensiva real de cada equipo (carreras/juego de la temporada)
4. Ajusta por park factor, ventaja de local y calidad del bullpen
5. Simula 10,000 instancias del juego con una distribución binomial negativa
6. Reporta probabilidades por mercado y registra las predicciones en un histórico

## Metodología

- **Motor de simulación:** Monte Carlo con distribución binomial negativa (capta la sobredispersión real de las carreras de béisbol, que una Poisson subestima)
- **Modelo de pitcheo:** FIP combinado abridor/bullpen ponderado por innings esperados, con factor de amortiguamiento calibrado. El bullpen usa la ERA real de relevistas de cada equipo (automática)
- **Modelo ofensivo:** carreras/juego ponderadas por reciencia (half-life configurable) y ajustadas por el split del lineup vs zurdo/derecho según la mano del abridor rival
- **Detección de valor:** compara las probabilidades del modelo contra las líneas reales del mercado, de-viguea el momio y resalta jugadas con valor esperado positivo (+EV)
- **Calibración:** parámetros ajustados para reproducir el promedio de carreras de la liga (~8.5 por juego)

## Mercados que cubre

Moneyline, run line, totales (juego completo y por equipo), primeras 5 entradas
(ML a 3 vías, run line ±0.5, totales), NRFI/YRFI (1ª entrada), marcadores más
probables y pronóstico de hits por bateador.

## Estructura

- `modelo_diario.py` — motor: analiza el slate completo, busca valor y registra predicciones
- `app.py` + `templates/` — web app local (FastAPI): `python app.py` → http://127.0.0.1:8000
- `generar_excel.py` — exporta el slate a Excel con formato (carpeta `exports/`)
- `generar_json.py` — genera el snapshot JSON que alimenta la página pública
- `analisis_comparacion.py` / `comparar_resultados.py` — backtesting contra resultados reales
- `tracker.py` — diario de apuestas: registro, calificación automática y ROI
- `valor.py` — módulo de detección de valor (+EV) contra líneas reales (The Odds API)
- `predicciones.csv` — histórico de predicciones (se genera al correr)

## Corridas automáticas + página web

Un GitHub Action (`.github/workflows/modelo-diario.yml`) corre el modelo todos
los días (~10:30am CDMX), acumula el histórico en `predicciones.csv` y publica
el snapshot en `docs/data/latest.json`, que sirve la página de **GitHub Pages**
(`docs/index.html`). Para activarla una sola vez: Settings → Pages → Deploy
from a branch → `main` / `docs`. También se puede disparar a mano desde la
pestaña Actions.

## Configuración

Opcional, para activar la detección de valor contra el mercado:

```
set ODDS_API_KEY=tu_clave   # de the-odds-api.com (plan free ~500 req/mes)
```

Sin clave, el modelo corre igual y solo omite la sección de valor. Para que la
corrida automática la use, agrégala como secret del repo (Settings → Secrets
and variables → Actions → `ODDS_API_KEY`).

## Tecnologías

Python · NumPy · MLB-StatsAPI · The Odds API

## Estado del proyecto

En desarrollo activo. Próximo módulo: validador de calibración (compara predicciones históricas contra resultados reales para medir Brier score, log-loss, curva de calibración y ROI/CLV).

## Autor

Roberto Rosas — Ingeniería Industrial y de Sistemas, Tecnológico de Monterrey

---

*Proyecto educativo de modelado estadístico y simulación.*