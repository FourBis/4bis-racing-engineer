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
    this.tagName = ({ liveView: 'BUTTON', analysisView: 'BUTTON', first: 'SELECT', second: 'SELECT', raceLap: 'SELECT', raceChannel: 'SELECT', cornerTopZones: 'BUTTON', cornerAllZones: 'BUTTON', cornerReplayToggle: 'BUTTON', cornerReplayRestart: 'BUTTON', cornerReplayTime: 'INPUT', cornerReplayRate: 'SELECT' })[id] || id.toUpperCase();
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
    if (['legendB', 'raceLegendB'].includes(id) && !elements.get(id).parentElement) elements.get(id).parentElement = new Element(id + 'Parent');
    return elements.get(id);
  };
  let fetchCalls = 0;
  let fetchResponse = null;
  const fetchUrls = [];
  let nextFrameId = 0;
  const animationFrames = new Map();
  const context = {
    document: {
      getElementById,
      querySelector: selector => getElementById(selector === '.skip' ? 'skipLink' : selector),
      createElement: tag => new Element(tag),
      createElementNS: (_namespace, tag) => new Element(tag),
    },
    fetch: url => {
      fetchCalls += 1;
      fetchUrls.push(String(url));
      if (fetchResponse) return Promise.resolve({ ok: true, json: () => Promise.resolve(fetchResponse) });
      throw new Error('fetch inesperado en la prueba');
    },
    setTimeout: () => 0,
    requestAnimationFrame: callback => { const id = ++nextFrameId; animationFrames.set(id, callback); return id; },
    cancelAnimationFrame: id => animationFrames.delete(id),
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
  vm.runInNewContext(`${withoutInitialPoll}\nglobalThis.__dashboard = { pedalReading, selectComparableLaps, renderLaps, renderFindings, renderRaceResults, drawCompare, setView, renderDriving, frameAtTime, clockTime, selectCornerZone, showCornerZones, toggleCornerReplay, cornerReplaySnapshot: () => ({ time: cornerReviewState.time, playing: cornerReviewState.playing, selected: cornerReviewState.selected }), runAnimationFrame: timestamp => { const [id, callback] = animationFrames.entries().next().value || []; if (callback) { animationFrames.delete(id); callback(timestamp); } } };`, context);
  return {
    dashboard: context.__dashboard,
    get: getElementById,
    fetchCalls: () => fetchCalls,
    fetchUrls,
    setFetchResponse: value => { fetchResponse = value; },
  };
}

function cornerResult(session = 'corner-session') {
  const zones = [
    { id: 't1', name: 'Curvas 1', start_pct: 10, end_pct: 20, delta_s: 1.2, first: { speed_mean_kmh: 120, brake_mean_pct: null }, second: { speed_mean_kmh: 110, brake_mean_pct: 42 }, observation: 'B entra con menos velocidad.', next_test: 'Comprobar el punto de liberación.' },
    { id: 't5', name: 'Curva 5', start_pct: 30, end_pct: 40, delta_s: 0.9, first: {}, second: {} },
    { id: 't8-t9', name: 'Curvas 8–9', start_pct: 50, end_pct: 60, delta_s: 0.7, first: {}, second: {} },
    { id: 't10a-t10b', name: 'Curvas 10a–10b', start_pct: 70, end_pct: 80, delta_s: 0.3, first: {}, second: {} },
  ];
  return {
    session_id: session, status: 'ok', laps: [], best: null, worst: null, delta_s: null, segments: [],
    comparison: { status: 'insufficient', points: [] }, limitations: [],
    corner_review: {
      available: true, note: 'Revisión aproximada; coordenadas de mapa, no GPS.',
      map: { view_box: [0, 0, 1000, 650], points: [[0, 100, 100], [50, 500, 325], [100, 900, 550]], labels: [{ name: 'Curva 1', progress_pct: 10, x: 150, y: 100 }] },
      zones, top_zone_ids: ['t1', 't5', 't8-t9'],
      replay: {
        first: { lap: 6, duration_s: 10, frames: [[0, 0, 100, 10, 20], [10, 100, 200, 80, 90]] },
        second: { lap: 10, duration_s: 20, frames: [[0, 0, 90, 30, 40], [20, 100, 190, null, 80]] },
      },
    },
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

test('renderRaceResults representa estado insuficiente y canales nulos sin inventar ceros', () => {
  const { dashboard, get } = loadDashboard();
  dashboard.renderRaceResults({
    session_id: 'session-1', status: 'insufficient',
    laps: [{ id: 'unknown', lap: 4, duration_s: null, review_eligible: false, exclusion_reason: 'incompleta' }],
    best: null, worst: null, delta_s: null, segments: [],
    comparison: { status: 'insufficient', points: [] }, limitations: ['Faltan vueltas analizables.'],
  });

  assert.equal(get('raceBest').textContent, '—');
  assert.equal(get('raceWorst').textContent, '—');
  assert.equal(get('raceGap').textContent, '—');
  assert.match(get('raceAvailability').textContent, /0 vueltas analizables/);
  assert.equal(get('raceLap').options.length, 1);
  assert.equal(get('raceLaps').children[0].children[1].textContent, '—');
  assert.match(get('raceLaps').children[0].children[3].textContent, /incompleta/);
  assert.match(get('raceStatus').textContent, /Sin comparación suficiente/);
});

test('renderRaceResults ofrece vueltas elegibles salvo la referencia y mantiene la selección manual entre polls', async () => {
  const { dashboard, get, setFetchResponse, fetchUrls } = loadDashboard();
  const automatic = {
    session_id: 'session-1', status: 'ok', selection: 'automatic',
    laps: [
      { id: 'best', lap: 1, duration_s: 90, review_eligible: true, context: [] },
      { id: 'slow', lap: 2, duration_s: 92, review_eligible: true, context: [] },
      { id: 'pit', lap: 3, duration_s: 96, review_eligible: false, exclusion_reason: 'paso por boxes', context: [] },
      { id: 'middle', lap: 4, duration_s: 91, review_eligible: true, context: [] },
    ],
    best: { id: 'best', lap: 1, duration_s: 90 },
    worst: { id: 'slow', lap: 2, duration_s: 92 }, delta_s: 2,
    segments: [], comparison: { status: 'insufficient', points: [] }, limitations: [],
  };
  dashboard.renderRaceResults(automatic);
  assert.deepEqual(get('raceLap').options.map(option => option.value), ['', 'slow', 'middle']);

  get('raceLap').value = 'middle';
  setFetchResponse({
    ...automatic, selection: 'manual',
    worst: { id: 'middle', lap: 4, duration_s: 91 }, delta_s: 1,
  });
  await get('raceLap').onchange();
  assert.equal(fetchUrls.at(-1), '/api/race-results.json?lap=middle');
  assert.equal(get('raceWorst').textContent, 'Vuelta 4 · 1:31.000');
  assert.equal(get('raceLap').value, 'middle');

  dashboard.renderRaceResults(automatic); // actualización periódica de /api/state
  assert.equal(get('raceWorst').textContent, 'Vuelta 4 · 1:31.000');
  assert.equal(get('raceLap').value, 'middle');

  dashboard.renderRaceResults({ ...automatic, session_id: 'session-2', worst: { id: 'slow', lap: 2, duration_s: 93 } });
  assert.equal(get('raceWorst').textContent, 'Vuelta 2 · 1:33.000');
  assert.equal(get('raceLap').value, '');
});

test('drawCompare con namespace race no modifica el gráfico legacy', () => {
  const { dashboard, get } = loadDashboard();
  const legacyChart = get('compareChart');
  const legacyPath = new Element('path');
  legacyChart.append(legacyPath);
  get('legendA').textContent = 'A anterior';
  get('legendB').parentElement.hidden = true;
  get('compareStatus').textContent = 'comparación previa';
  get('raceChannel').value = 'delta_s';

  dashboard.drawCompare({ status: 'ok', points: [
    { progress_pct: 25, delta_s: 1 },
    { progress_pct: 50, delta_s: 2 },
  ] }, 'race');

  assert.equal(legacyChart.children.length, 1);
  assert.equal(legacyChart.children[0], legacyPath);
  assert.equal(get('legendA').textContent, 'A anterior');
  assert.equal(get('legendB').parentElement.hidden, true);
  assert.equal(get('compareStatus').textContent, 'comparación previa');
  assert.ok(get('raceChart').children.length > 0);
});

test('frameAtTime sincroniza por tiempo y conserva null y la lectura final de cada vuelta', () => {
  const { dashboard } = loadDashboard();
  const a = { frames: [[0, 0, 100, 10, 20], [10, 100, 200, 80, 90]] };
  const b = { frames: [[0, 0, 90, 30, 40], [20, 100, 190, null, 80]] };
  assert.deepEqual({ ...dashboard.frameAtTime(a, 5) }, { elapsed_s: 5, progress_pct: 50, speed_kmh: 150, brake_pct: 45, throttle_pct: 55 });
  assert.deepEqual({ ...dashboard.frameAtTime(b, 5) }, { elapsed_s: 5, progress_pct: 25, speed_kmh: 115, brake_pct: null, throttle_pct: 50 });
  assert.deepEqual({ ...dashboard.frameAtTime(a, 10) }, { elapsed_s: 10, progress_pct: 100, speed_kmh: 200, brake_pct: 80, throttle_pct: 90 });
  assert.deepEqual({ ...dashboard.frameAtTime(a, 20) }, { elapsed_s: 20, progress_pct: 100, speed_kmh: null, brake_pct: null, throttle_pct: null });
  assert.deepEqual({ ...dashboard.frameAtTime(b, 20) }, { elapsed_s: 20, progress_pct: 100, speed_kmh: 190, brake_pct: null, throttle_pct: 80 });
});

test('clockTime redondea décimas antes de dividir minutos y segundos', () => {
  const { dashboard } = loadDashboard();
  assert.equal(dashboard.clockTime(59.96), '1:00.0');
  assert.equal(dashboard.clockTime(119.999), '2:00.0');
});

test('corner review muestra el top 3, resalta y busca la entrada más temprana; todas amplía la lista', () => {
  const { dashboard, get } = loadDashboard();
  const result = cornerResult();
  dashboard.renderRaceResults(result);
  assert.equal(get('cornerReviewMapPanel').hidden, false);
  assert.equal(get('raceDistanceDetails').open, false);
  assert.equal(get('cornerZoneList').children.length, 3);
  assert.equal(get('cornerZoneTitle').textContent, 'Curvas 1');
  assert.equal(get('cornerLapALabel').textContent, 'Referencia · vuelta 6');
  assert.equal(get('cornerLapBLabel').textContent, 'Vuelta revisada · vuelta 10');
  assert.match(get('cornerReplayNote').textContent, /mismo tiempo transcurrido desde meta de cada vuelta/);
  assert.match(html, /Zonas aproximadas: sus límites no son la entrada o salida exacta de cada curva/);
  assert.equal(get('cornerZoneObservation').textContent, 'B entra con menos velocidad.');
  assert.equal(get('cornerZoneNextTest').textContent, 'Comprobar el punto de liberación.');
  const brakeStat = get('cornerZoneStats').children.find(item => item.textContent === 'Freno medio observado');
  assert.equal(brakeStat.children[0].textContent, 'A — → B 42.0 %');

  dashboard.showCornerZones(true);
  dashboard.selectCornerZone('t10a-t10b');
  assert.equal(dashboard.cornerReplaySnapshot().time, 7);
  assert.equal(get('cornerZoneTitle').textContent, 'Curvas 10a–10b');
  assert.equal(get('cornerMapFocus').getAttribute('points'), '660,415 740,460');
  assert.equal(get('cornerZoneList').children.length, 4);
  get('cornerReplayTime').value = '5';
  get('cornerReplayTime').oninput();
  assert.equal(Number(get('cornerMarkerALabel').getAttribute('y')), Number(get('cornerMarkerA').getAttribute('cy')) - 18);
  assert.equal(Number(get('cornerMarkerBLabel').getAttribute('y')), Number(get('cornerMarkerB').getAttribute('cy')) + 30);
});

test('corner review no inventa pérdidas positivas cuando top_zone_ids viene vacío', () => {
  const { dashboard, get } = loadDashboard();
  const result = cornerResult();
  result.corner_review.top_zone_ids = [];
  dashboard.renderRaceResults(result);
  assert.equal(get('cornerZoneList').children.length, 0);
  assert.equal(get('cornerZoneEmpty').hidden, false);
  assert.match(html, /id="cornerZoneEmpty"[^>]*>No se identificaron pérdidas positivas por zona/);
  dashboard.showCornerZones(true);
  assert.equal(get('cornerZoneEmpty').hidden, true);
  assert.equal(get('cornerZoneList').children.length, 4);
});

test('corner review conserva replay en polls idénticos, lo pausa al cambiar sesión y abre fallback sin mapa', () => {
  const { dashboard, get } = loadDashboard();
  const result = cornerResult();
  dashboard.renderRaceResults(result);
  assert.equal(dashboard.cornerReplaySnapshot().playing, false, 'el replay debe iniciar pausado');
  dashboard.toggleCornerReplay();
  assert.equal(dashboard.cornerReplaySnapshot().playing, true);
  dashboard.renderRaceResults(result);
  assert.equal(dashboard.cornerReplaySnapshot().playing, true);
  dashboard.renderRaceResults(cornerResult('new-session'));
  assert.equal(dashboard.cornerReplaySnapshot().playing, false);
  assert.equal(dashboard.cornerReplaySnapshot().time, 0);

  dashboard.renderRaceResults({ ...cornerResult('no-map'), corner_review: { available: false, reason: 'Falta el mapa oficial de la pista.' } });
  assert.equal(get('cornerReviewMapPanel').hidden, true);
  assert.equal(get('cornerReviewFallback').textContent, 'Falta el mapa oficial de la pista.');
  assert.equal(get('raceDistanceDetails').open, true);
});
