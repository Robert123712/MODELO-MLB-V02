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

import re
import subprocess
import sys

TEMPLATE = "templates/index.html"
REPO_FALLBACK = "Robert123712/MODELO-MLB-V02"
SALIDA = "docs/index.html"

CONTROLES = '''    <div class="controls">
      <div style="flex:1 1 260px">
        <label>Snapshot</label>
        <p id="snapshot-info" style="font-size:14px; color:var(--ink-2); font-weight:600;">Cargando…</p>
        <p style="font-size:12px; color:var(--ink-3); margin-top:3px;">Se actualiza solo cada día (~10:30am CDMX). Con el botón lo corres cuando quieras.</p>
      </div>
      <button id="btn-run" class="btn-primary">⟳ Correr partidos</button>
      <button id="btn-reload" class="btn-ghost" title="Volver a bajar el último resultado">Recargar</button>
      <p id="run-estado" class="calib hidden"></p>

      <div id="panel-token" class="panel-token hidden">
        <p class="pt-titulo">Para correr los partidos desde aquí</p>
        <p class="pt-texto">
          La página es estática (GitHub Pages) y no puede ejecutar el modelo por sí sola:
          lo corre GitHub Actions. Pégale un <strong>token</strong> tuyo y el botón lo dispara solo.
        </p>
        <ol class="pt-pasos">
          <li>Abre <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">github.com/settings/personal-access-tokens/new</a></li>
          <li>Repository access: <strong>Only select repositories</strong> → este repo</li>
          <li>Permissions → Repository → <strong>Actions: Read and write</strong></li>
          <li>Genera el token y pégalo aquí abajo</li>
        </ol>
        <input id="input-token" type="password" placeholder="github_pat_..." autocomplete="off" spellcheck="false">
        <div class="pt-botones">
          <button id="btn-token-guardar" class="btn-primary">Guardar</button>
          <button id="btn-token-cancelar" class="btn-ghost">Cancelar</button>
          <button id="btn-token-borrar" class="btn-ghost">Borrar token</button>
          <a id="link-actions" href="#" target="_blank" rel="noopener" class="pt-link">o córrelo desde Actions →</a>
        </div>
        <p class="pt-nota">
          El token se guarda solo en este navegador (localStorage), nunca en el repositorio.
          Usa uno de alcance mínimo como el de arriba y ponle fecha de expiración.
        </p>
      </div>

      <p id="calibracion-info" class="calib hidden"></p>
    </div>

'''

ESTILOS_EXTRA = '''
    /* panel del token (solo en la pagina publica) */
    .panel-token {
      width: 100%; margin-top: 12px; padding: 14px 16px;
      background: var(--surface-2); border: 1px solid var(--border); border-radius: 10px;
    }
    .panel-token .pt-titulo { font-size: 13.5px; font-weight: 700; margin-bottom: 6px; }
    .panel-token .pt-texto { font-size: 13px; color: var(--ink-2); margin-bottom: 8px; }
    .panel-token .pt-pasos { font-size: 13px; color: var(--ink-2); margin: 0 0 10px 18px; }
    .panel-token .pt-pasos li { margin-bottom: 3px; }
    .panel-token input {
      width: 100%; background: var(--bg); border: 1px solid var(--border); color: var(--ink);
      border-radius: 8px; padding: 9px 11px; font: inherit; font-family: ui-monospace, monospace;
      font-size: 13px; margin-bottom: 10px;
    }
    .panel-token .pt-botones { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .panel-token .pt-link { font-size: 12.5px; margin-left: auto; }
    .panel-token .pt-nota { font-size: 11.5px; color: var(--ink-3); margin-top: 10px; line-height: 1.5; }
'''

ARRANQUE_JS = '''const $ = id => document.getElementById(id);
const resultados = $('resultados'), gameCards = $('game-cards'), skeleton = $('skeleton');
const emptyState = $('empty-state'), errorDiv = $('error');
const slateSummary = $('slate-summary'), calibInfo = $('calibracion-info'), statusInd = $('status-indicator');
const snapshotInfo = $('snapshot-info'), runEstado = $('run-estado');
const btnRun = $('btn-run'), btnReload = $('btn-reload'), panelToken = $('panel-token');

/* ---------- configuración ---------- */
const REPO = 'REPO_PLACEHOLDER';
const WORKFLOW = 'modelo-diario.yml';
// raw.githubusercontent publica el commit al instante; la copia de Pages tarda
// ~1 min más en desplegarse. Se intenta la fresca primero.
const URL_RAW = `https://raw.githubusercontent.com/${REPO}/main/docs/data/latest.json`;
const URL_ACTIONS = `https://github.com/${REPO}/actions/workflows/${WORKFLOW}`;
const CLAVE_TOKEN = 'mlb_modelo_token';

const ESPERA_MAX_MS = 8 * 60 * 1000;   // el Action tarda ~2 min; margen de sobra
const INTERVALO_MS = 12 * 1000;

let generadoActual = null;

function setStatus(cls, txt) { statusInd.innerHTML = `<span class="dot ${cls}"></span> ${txt}`; }
function estado(txt) {
  runEstado.textContent = txt || '';
  runEstado.classList.toggle('hidden', !txt);
}

/* ---------- datos ---------- */
async function traerSnapshot() {
  const t = Date.now();
  for (const url of [`${URL_RAW}?t=${t}`, `data/latest.json?t=${t}`]) {
    try {
      const res = await fetch(url, { cache: 'no-store' });
      if (res.ok) return await res.json();
    } catch (e) { /* intenta la siguiente */ }
  }
  return null;
}

function pintar(data) {
  const gen = data.generado_en ? new Date(data.generado_en) : null;
  generadoActual = data.generado_en || null;
  snapshotInfo.textContent = `Slate del ${data.fecha}` +
    (gen ? ` · generado ${gen.toLocaleString('es-MX', { dateStyle: 'medium', timeStyle: 'short' })}` : '');
  renderResultados(data);
}

async function cargar(silencioso) {
  if (!silencioso) skeleton.classList.remove('hidden');
  setStatus('run', 'Cargando…');
  const data = await traerSnapshot();
  if (!data) {
    snapshotInfo.textContent = 'Sin datos todavía';
    emptyState.classList.remove('hidden');
    setStatus('err', 'Sin corrida');
    skeleton.classList.add('hidden');
    return;
  }
  // El render va fuera del try de la descarga: si algo truena aquí es un bug de
  // la página, no falta de datos, y hay que verlo en vez de decir "sin corrida".
  try {
    emptyState.classList.add('hidden');
    errorDiv.classList.add('hidden');
    pintar(data);
  } catch (err) {
    errorDiv.textContent = '⚠ Error al dibujar los resultados: ' + err.message;
    errorDiv.classList.remove('hidden');
    setStatus('err', 'Error');
  } finally {
    skeleton.classList.add('hidden');
  }
}

/* ---------- correr los partidos ---------- */
/* La página es estática: no puede ejecutar el modelo. Lo que hace es disparar
   el workflow de GitHub Actions (que sí corre Python) y esperar el resultado. */

function mostrarPanel(mostrar) {
  panelToken.classList.toggle('hidden', !mostrar);
  if (mostrar) {
    $('link-actions').href = URL_ACTIONS;
    $('btn-token-borrar').classList.toggle('hidden', !localStorage.getItem(CLAVE_TOKEN));
    $('input-token').focus();
  }
}

async function correrPartidos() {
  const token = localStorage.getItem(CLAVE_TOKEN);
  if (!token) { mostrarPanel(true); return; }

  btnRun.disabled = true;
  errorDiv.classList.add('hidden');
  setStatus('run', 'Ejecutando…');
  estado('Pidiendo la corrida a GitHub Actions…');

  let res;
  try {
    res = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ref: 'main' }),
    });
  } catch (err) {
    btnRun.disabled = false;
    estado('');
    errorDiv.textContent = '⚠ No se pudo contactar a GitHub: ' + err.message;
    errorDiv.classList.remove('hidden');
    setStatus('err', 'Error');
    return;
  }

  if (res.status === 401 || res.status === 403) {
    btnRun.disabled = false;
    estado('');
    localStorage.removeItem(CLAVE_TOKEN);
    errorDiv.innerHTML = '⚠ El token no sirve o le faltan permisos (necesita <strong>Actions: Read and write</strong> en este repo). Vuelve a configurarlo.';
    errorDiv.classList.remove('hidden');
    setStatus('err', 'Token inválido');
    mostrarPanel(true);
    return;
  }
  if (!res.ok && res.status !== 204) {
    btnRun.disabled = false;
    estado('');
    errorDiv.textContent = `⚠ GitHub respondió ${res.status}. Puedes correrlo desde la pestaña Actions del repo.`;
    errorDiv.classList.remove('hidden');
    setStatus('err', 'Error');
    return;
  }

  await esperarResultado();
}

async function esperarResultado() {
  const previo = generadoActual;
  const inicio = Date.now();

  while (Date.now() - inicio < ESPERA_MAX_MS) {
    const seg = Math.round((Date.now() - inicio) / 1000);
    estado(`Simulando el slate… ${seg}s (suele tardar ~2 min)`);
    await new Promise(r => setTimeout(r, INTERVALO_MS));

    const data = await traerSnapshot();
    // Se compara contra el snapshot que ya teníamos: cuando cambia, la corrida
    // termino y publico datos nuevos.
    if (data && data.generado_en && data.generado_en !== previo) {
      pintar(data);
      estado('');
      btnRun.disabled = false;
      setStatus('ok', `${data.juegos.length} juegos · recién corrido`);
      return;
    }
  }

  estado('');
  btnRun.disabled = false;
  errorDiv.innerHTML = `⚠ La corrida tardó más de lo normal. Revisa cómo va en <a href="${URL_ACTIONS}" target="_blank" rel="noopener">Actions</a> y luego dale Recargar.`;
  errorDiv.classList.remove('hidden');
  setStatus('err', 'Sin respuesta');
}

/* ---------- eventos ---------- */
btnRun.addEventListener('click', correrPartidos);
btnReload.addEventListener('click', () => cargar(false));
$('btn-token-cancelar').addEventListener('click', () => mostrarPanel(false));
$('btn-token-borrar').addEventListener('click', () => {
  localStorage.removeItem(CLAVE_TOKEN);
  $('input-token').value = '';
  mostrarPanel(false);
});
$('btn-token-guardar').addEventListener('click', () => {
  const t = $('input-token').value.trim();
  if (!t) return;
  localStorage.setItem(CLAVE_TOKEN, t);
  $('input-token').value = '';
  mostrarPanel(false);
  correrPartidos();
});
$('input-token').addEventListener('keydown', e => {
  if (e.key === 'Enter') $('btn-token-guardar').click();
});

cargar(false);

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


def _repo_github():
    """owner/repo del remoto, para que el boton apunte al workflow correcto."""
    try:
        url = subprocess.run(["git", "config", "--get", "remote.origin.url"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return REPO_FALLBACK


def main():
    with open(TEMPLATE, encoding="utf-8") as f:
        html = f.read()

    html = _reemplazar(html, '    <div class="controls">', '    <div id="skeleton"',
                       CONTROLES, "los controles")
    # estilos propios de la pagina publica (panel del token)
    if "  </style>" not in html:
        raise SystemExit("❌ No encontre el cierre de <style> en el template.")
    html = html.replace("  </style>", ESTILOS_EXTRA + "  </style>", 1)
    if VACIO_LOCAL not in html:
        raise SystemExit("❌ No encontre el estado vacio en el template.")
    html = html.replace(VACIO_LOCAL, VACIO_PAGES)
    # Se sustituye SOLO el arranque: los helpers de formato y el render se conservan
    html = _reemplazar(html, 'const $ = id => document.getElementById(id);',
                       '/* ---------- helpers de formato ---------- */',
                       ARRANQUE_JS.replace("REPO_PLACEHOLDER", _repo_github()),
                       "el arranque del JS")

    # Comprobacion: sin estas piezas la pagina se cae en silencio
    faltan = [n for n in ("const pct =", "function momio", "function momioCasa",
                          "const fx =", "function histograma",
                          "function renderResultados", "function cardJuego",
                          "id=\"btn-run\"", "id=\"panel-token\"",
                          "function correrPartidos") if n not in html]
    if "REPO_PLACEHOLDER" in html:
        raise SystemExit("❌ Quedo el placeholder del repo sin sustituir.")
    if faltan:
        raise SystemExit(f"❌ La pagina generada perderia: {', '.join(faltan)}")

    with open(SALIDA, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✅ {SALIDA} regenerado desde {TEMPLATE} ({html.count(chr(10))} lineas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
