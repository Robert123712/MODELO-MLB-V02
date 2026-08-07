# ============================================================
# SINCRONIZA docs/index.html DESDE templates/index.html
#
# Las dos paginas son el MISMO dashboard: mismo CSS, mismas tablas, mismo
# render. Solo cambia el arranque:
#   templates/  -> POST a /api/simular (app.py corriendo en local)
#   docs/       -> fetch de data/latest.json (snapshot en GitHub Pages)
#
# Mantenerlas a mano garantiza que se desincronicen. Edita SIEMPRE el template
# y corre este script:  python sync_docs.py
# ============================================================

import sys

TEMPLATE = "templates/index.html"
SALIDA = "docs/index.html"

CONTROLES = '''    <div class="controls">
      <div>
        <label>Snapshot diario</label>
        <p id="snapshot-info" style="font-size:14px; color:var(--ink-2); font-weight:600;">Cargando…</p>
        <p style="font-size:12px; color:var(--ink-3); margin-top:3px;">Se actualiza automáticamente cada día (~10:30am CDMX) con los abridores anunciados</p>
      </div>
      <p id="calibracion-info" class="calib hidden"></p>
    </div>

'''

ARRANQUE_JS = '''const $ = id => document.getElementById(id);
const resultados = $('resultados'), gameCards = $('game-cards'), skeleton = $('skeleton');
const emptyState = $('empty-state'), errorDiv = $('error');
const slateSummary = $('slate-summary'), calibInfo = $('calibracion-info'), statusInd = $('status-indicator');
const snapshotInfo = $('snapshot-info');

function setStatus(cls, txt) { statusInd.innerHTML = `<span class="dot ${cls}"></span> ${txt}`; }

async function cargar() {
  skeleton.classList.remove('hidden');
  setStatus('run', 'Cargando…');
  let data;
  try {
    const res = await fetch('data/latest.json', { cache: 'no-store' });
    if (!res.ok) throw new Error('sin datos');
    data = await res.json();
  } catch (err) {
    snapshotInfo.textContent = 'Sin datos todavía';
    emptyState.classList.remove('hidden');
    setStatus('err', 'Sin corrida');
    skeleton.classList.add('hidden');
    return;
  }
  // El render va fuera del try de la descarga: si algo truena aqui es un bug de
  // la pagina, no falta de datos, y hay que verlo en vez de decir "sin corrida".
  try {
    const gen = data.generado_en ? new Date(data.generado_en) : null;
    snapshotInfo.textContent = `Slate del ${data.fecha}` +
      (gen ? ` · generado ${gen.toLocaleString('es-MX', { dateStyle: 'medium', timeStyle: 'short' })}` : '');
    renderResultados(data);
  } catch (err) {
    errorDiv.textContent = '⚠ Error al dibujar los resultados: ' + err.message;
    errorDiv.classList.remove('hidden');
    setStatus('err', 'Error');
  } finally {
    skeleton.classList.add('hidden');
  }
}
cargar();

'''

VACIO_LOCAL = ('      <p>Selecciona una fecha y presiona <strong>Correr modelo</strong></p>\n'
               '      <p style="color:var(--ink-3); font-size:13px; margin-top:4px;">'
               'Aquí aparecerán las predicciones del día</p>')
VACIO_PAGES = ('      <p>Aún no hay una corrida publicada</p>\n'
               '      <p style="color:var(--ink-3); font-size:13px; margin-top:4px;">'
               'La primera corrida automática aparecerá aquí (o dispárala desde la pestaña Actions del repo)</p>')


def _reemplazar(texto, desde, hasta, nuevo, que):
    """Sustituye el bloque [desde, hasta) por 'nuevo'. Truena si no encuentra
    los anclajes: mejor fallar que escribir una pagina rota."""
    try:
        i = texto.index(desde)
        f = texto.index(hasta)
    except ValueError:
        raise SystemExit(f"❌ No encontre el anclaje de {que} en {TEMPLATE}. "
                         "¿Cambio la estructura? Actualiza sync_docs.py.")
    return texto[:i] + nuevo + texto[f:]


def main():
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()

    html = _reemplazar(html, '    <div class="controls">', '    <div id="skeleton"',
                       CONTROLES, "los controles")
    if VACIO_LOCAL not in html:
        raise SystemExit("❌ No encontre el estado vacio en el template.")
    html = html.replace(VACIO_LOCAL, VACIO_PAGES)
    # Se sustituye SOLO el arranque: los helpers de formato y el render se conservan
    html = _reemplazar(html, 'const $ = id => document.getElementById(id);',
                       '/* ---------- helpers de formato ---------- */',
                       ARRANQUE_JS, "el arranque del JS")

    # Comprobacion: sin estas piezas la pagina se cae en silencio
    faltan = [n for n in ("const pct =", "function momio", "const fx =",
                          "function histograma", "function renderResultados",
                          "function cardJuego") if n not in html]
    if faltan:
        raise SystemExit(f"❌ La pagina generada perderia: {', '.join(faltan)}")

    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ {SALIDA} regenerado desde {TEMPLATE} ({html.count(chr(10))} lineas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
