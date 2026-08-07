---
name: analizar-slate
description: Procedimiento para leer el slate del día del modelo MLB y sacar conclusiones útiles — qué jugadas destacan, qué discrepancias con el mercado son señal y cuáles son ruido conocido del modelo. Usar cuando el usuario pida analizar el slate, ver los juegos de hoy, pedir insights o preguntar en qué apostar.
---

# Analizar el slate del día

## De dónde salen los datos

El snapshot vive en `docs/data/latest.json` (lo genera la corrida automática y
lo sirve la página). Para leer el más reciente:

```bash
git fetch origin main -q && git checkout origin/main -- docs/data/ predicciones.csv
python -c "import json; d=json.load(open('docs/data/latest.json')); print(d['fecha'], d['generado_en'], len(d['juegos']))"
```

**Verifica siempre la fecha del snapshot antes de analizar.** Si la sesión lleva
horas abierta, el archivo local puede ser de ayer — es un error que ya se cometió.

Si `hay_odds` es `false`, la corrida no vio el mercado: entonces tienes
probabilidades del modelo, **no valor**. Dilo explícitamente en vez de sugerir
apuestas: valor = probabilidad del modelo vs la del book, y sin líneas esa
comparación no existe.

## Qué mirar, en orden

1. **Salud del slate**: `total_promedio_slate` debe rondar 8.5. Muy lejos =
   problema de calibración, no una oportunidad.
2. **Favoritos más marcados** y **totales extremos** (los `total_esp` más alto y
   más bajo).
3. **De dónde viene cada favorito**: compara `fip_v`/`fip_c` contra `rg_v`/`rg_c`.
   - Favorito por **pitcheo** (mejor FIP) → discrepancia defendible contra el mercado.
   - Favorito por **ofensiva** contra peor pitcheo → sospechoso, ver sesgos abajo.
4. **F5**: es donde el modelo tiene más ventaja estructural (elimina al bullpen,
   los books precian esas derivadas con menos cuidado). Busca desajustes grandes
   de abridores.
5. **NRFI/YRFI** y coherencia: un juego con total bajo debería tener NRFI alto;
   si dos mercados del mismo juego apuntan al mismo lado, la señal es más creíble.
6. **Jugadas +EV** (solo si `hay_odds`): reporta mercado, pick, momio, EV y libro.

## Sesgos conocidos del modelo — banderas obligatorias

Avísalos cuando aparezcan, aunque el número se vea atractivo:

- **Ofensivas infladas**: `rg` > ~5.3 suele ser racha, no talento (la mezcla
  reciencia/temporada lo atenúa pero no lo elimina).
- **Sin ajuste por calendario**: el modelo no sabe contra quién anotó esas
  carreras un equipo.
- **Sin clima**: nada de viento ni temperatura, que mueven los totales.
- **Sin lesiones ni lineup del día**: el mercado sí los tiene. En discrepancias
  grandes contra el book, la primera hipótesis es información que nos falta.
- **`estimado: true`**: uno de los abridores no tenía stats y va a nivel de
  reemplazo — más incertidumbre de la normal.
- **Bullpen day / opener**: si el "abridor" es un relevista, el juego está mal
  modelado. Sugiere ignorarlo.

## Cómo reportar

Lidera con las 2-3 jugadas más claras y **por qué** (el mecanismo: qué
desajuste las produce), no solo el porcentaje. Después los extremos de totales,
después las banderas. Sé explícito sobre la incertidumbre: son probabilidades de
un modelo con sesgos conocidos, no recomendaciones.

Si el usuario pregunta "¿en qué le voy?", responde con lo que el modelo ve y sus
limitaciones — no conviertas una probabilidad en un consejo financiero.
