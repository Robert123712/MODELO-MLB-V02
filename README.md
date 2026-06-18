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

## Estructura

- `modelo_diario.py` — script principal: analiza el slate completo, busca valor y registra predicciones
- `valor.py` — módulo de detección de valor (+EV) contra líneas reales (The Odds API)
- `predicciones.csv` — histórico de predicciones (se genera al correr)

## Configuración

Opcional, para activar la detección de valor contra el mercado:

```
set ODDS_API_KEY=tu_clave   # de the-odds-api.com (plan free ~500 req/mes)
```

Sin clave, el modelo corre igual y solo omite la sección de valor.

## Tecnologías

Python · NumPy · MLB-StatsAPI · The Odds API

## Estado del proyecto

En desarrollo activo. Próximo módulo: validador de calibración (compara predicciones históricas contra resultados reales para medir Brier score, log-loss, curva de calibración y ROI/CLV).

## Autor

Roberto Rosas — Ingeniería Industrial y de Sistemas, Tecnológico de Monterrey

---

*Proyecto educativo de modelado estadístico y simulación.*