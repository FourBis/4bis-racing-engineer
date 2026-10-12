const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'racing_engineer', 'dashboard.html'), 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
assert.ok(script, 'dashboard.html debe contener su script inline');

class Element {
  constructor(id = '') {
    this.id = id;
    this.tagName = ({ liveView: 'BUTTON', analysisView: 'BUTTON', first: 'SELECT', second: 'SELECT' })[id] || id.toUpperCase();
    this.attributes = {};
    this.children = [];
    this.hidden = false;
    this.open = false;
    this.textContent = '';
    this.style = {};
    this.dataset = {};
    this.parentElement = null;
    this._value = '';
    this._selectedIndex = -1;
  }
  get value() { return this._value; }
  set value(value) {
    this._value = String(value);
    this._selectedIndex = this.children.findIndex(child => child.value === this._value);
  }
  get options() { return this.children; }
  get firstElementChild() { return this.children[0] ?? null; }
  get lastElementChild() { return this.children.at(-1) ?? null; }
  get selectedIndex() { return this._selectedIndex; }
  set selectedIndex(index) {
    this._selectedIndex = index;
    this._value = this.children[index]?.value ?? '';
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(name, callback) { this['on' + name] = callback; }
  click() { return this.onclick?.({ currentTarget: this }); }
  append(...items) {
    for (const item of items) item.parentElement = this;
    this.children.push(...items);
  }
  replaceChildren(...items) {
    this.children = items;
    for (const item of items) item.parentElement = this;
    if (this.tagName === 'SELECT') { this._value = ''; this._selectedIndex = -1; }
  }
}

function loadDashboard() {
  const elements = new Map();
  const getElementById = id => {
    if (!elements.has(id)) elements.set(id, new Element(id));
    if (id === 'legendB' && !elements.get(id).parentElement) elements.get(id).parentElement = new Element('legendParent');
    return elements.get(id);
  };
  let fetchCalls = 0;
  let fetchResponse = null;
  const context = {
    document: {
      getElementById,
      querySelector: selector => getElementById(selector === '.skip' ? 'skipLink' : selector),
      createElement: tag => new Element(tag),
      createElementNS: (_namespace, tag) => new Element(tag),
    },
    fetch: () => {
      fetchCalls += 1;
      if (fetchResponse) return Promise.resolve({ ok: true, json: () => Promise.resolve(fetchResponse) });
      throw new Error('fetch inesperado en la prueba');
    },
    setTimeout: () => 0,
    console,
    Number,
    Math,
    JSON,
    Date,
    Set,
    Array,
    Object,
    Error,
    encodeURIComponent,
  };
  // Solo se omite el polling inicial, para que evaluar el script no haga requests.
  const withoutInitialPoll = script.replace(/\bpoll\(\);\s*$/, '');
  vm.runInNewContext(`${withoutInitialPoll}\nglobalThis.__dashboard = { pedalReading, selectComparableLaps, renderLaps, renderFindings, drawCompare, setView, renderDriving };`, context);
  return {
    dashboard: context.__dashboard,
    get: getElementById,
    fetchCalls: () => fetchCalls,
    setFetchResponse: value => { fetchResponse = value; },
  };
}

test('pedalReading conserva huecos y normaliza señales finitas', () => {
  const { dashboard } = loadDashboard();
  assert.deepEqual({ ...dashboard.pedalReading(0.94, true) }, { label: '94', value: 94 });

  for (const value of [null, undefined, NaN, Infinity, -Infinity, -0.01, 1.01]) {
    assert.deepEqual({ ...dashboard.pedalReading(value, true) }, { label: '—', value: null });
  }
  assert.deepEqual({ ...dashboard.pedalReading(0.94, false) }, { label: '—', value: null });
});

test('selectComparableLaps conserva solo vueltas completas, elegibles y con duración válida', () => {
  const { dashboard } = loadDashboard();
  const laps = [
    { id: 'ok', complete: true, eligible: true, duration_s: 94 },
    { id: 'incomplete', complete: false, eligible: true, duration_s: 90 },
    { id: 'ineligible', complete: true, eligible: false, duration_s: 91 },
    { id: 'zero', complete: true, eligible: true, duration_s: 0 },
    { id: 'missing', complete: true, eligible: true, duration_s: null },
    { id: 'nan', complete: true, eligible: true, duration_s: NaN },
    { id: 'infinite', complete: true, eligible: true, duration_s: Infinity },
  ];
  assert.deepEqual(dashboard.selectComparableLaps(laps).map(lap => lap.id), ['ok']);
});

test('renderLaps mantiene la selección entre actualizaciones y excluye segmentos de duración cero', () => {
  const { dashboard, get } = loadDashboard();
  const laps = [
    { id: 'lap-a', lap: 1, complete: true, eligible: true, duration_s: 94 },
    { id: 'lap-b', lap: 2, complete: true, eligible: true, duration_s: 95 },
    { id: 'segment', lap: 3, complete: true, eligible: true, duration_s: 0 },
  ];

  dashboard.renderLaps(laps);
  get('first').value = 'lap-b';
  get('second').value = 'lap-a';
  dashboard.renderLaps(laps);

  assert.equal(get('first').value, 'lap-b');
  assert.equal(get('second').value, 'lap-a');
  assert.deepEqual(get('first').options.map(option => option.value), ['lap-a', 'lap-b']);
});

test('setView cambia panel y estado accesible sin solicitudes de red', () => {
  const { dashboard, get, fetchCalls } = loadDashboard();
  const driveButton = get('liveView');
  const analysisButton = get('analysisView');
  assert.match(html, /<button\b[^>]*\bid="liveView"/);
  assert.match(html, /<button\b[^>]*\bid="analysisView"/);

  analysisButton.click();
  assert.equal(get('drivePanel').hidden, true);
  assert.equal(get('analysisPanel').hidden, false);
  assert.equal(analysisButton.getAttribute('aria-pressed'), 'true');
  assert.equal(driveButton.getAttribute('aria-pressed'), 'false');

  driveButton.click();
  assert.equal(get('drivePanel').hidden, false);
  assert.equal(get('analysisPanel').hidden, true);
  assert.equal(driveButton.getAttribute('aria-pressed'), 'true');
  assert.equal(analysisButton.getAttribute('aria-pressed'), 'false');
  assert.equal(fetchCalls(), 0);
});

test('renderDriving borra pedales y avisos cuando LIVE está obsoleto, desconectado o finalizado', () => {
  const { dashboard, get } = loadDashboard();
  const fresh = {
    mode: 'live', connected: true, fresh: true, running: true,
    samples: [{ brake: 0.2, throttle: 0.94 }],
    navigation: {
      track: { name: 'Road Atlanta', car: 'GT4' }, delta_s: 0.4,
      reference: { lap: 3, time_s: 94 },
      next: { location_basis: 'official_map_estimate', corner_name: 'Curva 5', cue: 'Frenar', reference_brake_peak: 0.8, distance_m: 120 },
    },
  };
  dashboard.renderDriving(fresh);
  assert.equal(get('brakeValue').textContent, '20');
  assert.equal(get('throttleValue').textContent, '94');
  assert.equal(get('coachCorner').textContent, 'Curva 5');

  for (const state of [
    { ...fresh, fresh: false },
    { ...fresh, connected: false },
    { ...fresh, running: false },
  ]) {
    dashboard.renderDriving(state);
    assert.equal(get('brakeValue').textContent, '—');
    assert.equal(get('throttleValue').textContent, '—');
    assert.equal(get('brakeMeter').getAttribute('aria-valuenow'), null);
    assert.equal(get('throttleMeter').getAttribute('aria-valuenow'), null);
    assert.equal(get('brakeFill').style.width, '0%');
    assert.equal(get('throttleFill').style.width, '0%');
    assert.equal(get('coachCue').textContent, 'Las indicaciones se pausaron. Revisa las trazas guardadas en Análisis.');
    assert.equal(get('coachCorner').textContent, 'Esperando pista');
    assert.equal(get('coachDistance').textContent, 'Sin indicación de frenada');
    assert.equal(get('driveDelta').textContent, '—');
  }
});

test('renderFindings conserva los detalles abiertos y reutiliza el DOM si el informe no cambió', () => {
  const { dashboard, get } = loadDashboard();
  const finding = { observation: 'Frenada observada', confidence: 0.8, interpretation: 'Entrada estable' };
  dashboard.renderFindings([finding]);
  const initialCard = get('findings').children[0];
  const initialDetails = initialCard.children[1];
  initialDetails.open = true;

  dashboard.renderFindings([finding]);
  assert.equal(get('findings').children[0], initialCard);

  dashboard.renderFindings([{ ...finding, interpretation: 'Entrada progresiva' }]);
  const updatedCard = get('findings').children[0];
  assert.equal(updatedCard.children[1].open, true);
  assert.match(updatedCard.children[1].children.map(child => child.textContent).join(' '), /Entrada progresiva/);
});

test('drawCompare mantiene escala pedal de 0 a 100 y los selectores invalidan el gráfico anterior', async () => {
  for (const channel of ['brake', 'throttle']) {
    const { dashboard, get, setFetchResponse, fetchCalls } = loadDashboard();
    get('first').value = 'lap-a';
    get('second').value = 'lap-b';
    get('compareChannel').value = channel;
    setFetchResponse({ status: 'ok', points: [
      { progress_pct: 0, first: { [channel]: 0.2 }, second: { [channel]: 0.9 } },
      { progress_pct: 100, first: { [channel]: 0.3 }, second: { [channel]: 0.8 } },
    ] });

    await get('compare').click();
    assert.equal(fetchCalls(), 1, `la solicitud de ${channel} debe completarse`);
    const axisLabels = get('compareChart').children.filter(child => child.tagName.toLowerCase() === 'text').map(child => Number(child.textContent));
    assert.ok(axisLabels.some(value => value === 100), `${channel}: escala debe incluir 100; ticks: ${axisLabels}`);
    assert.ok(axisLabels.some(value => value === 0), `${channel}: escala debe incluir 0; ticks: ${axisLabels}`);

    assert.ok(get('compareChart').children.length > 0);
    get('first').onchange?.();
    assert.equal(get('compareChart').children.length, 0);

    await get('compare').click();
    assert.ok(get('compareChart').children.length > 0);
    get('second').onchange?.();
    assert.equal(get('compareChart').children.length, 0);
  }
});
