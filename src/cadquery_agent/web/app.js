import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { TransformControls } from 'three/addons/controls/TransformControls.js';

const $ = (selector) => document.querySelector(selector);

const STORAGE_KEY = 'cadquery-project-id';
const MODELS_KEY = 'cadquery-models';
const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp'];
const MAX_RECORDING_SECONDS = 60;
const METER_BARS = 22;
const PART_COLORS = [0x7fa3ff, 0xffb86b, 0x7fd8a8, 0xff8fa3, 0xc49bff, 0x5fd4e8, 0xf0d264];
const narrowScreen = window.matchMedia('(max-width: 1000px)');
const STARTER_CODE = `import cadquery as cq

# Change these values, then run the code
length = 40
width = 30
height = 10

result = cq.Workplane('XY').box(length, width, height)`;

const ui = {
  history: $('#history'), nameInput: $('#project-name'), model: $('#model'), modelLabel: $('#model-label'),
  title: $('#revision-title'), size: $('#size'), step: $('#step'), stl: $('#stl'), empty: $('#empty'),
  viewerLoading: $('#viewer-loading'), busyBar: $('#busy-bar'),
  progress: $('#progress'), progressTitle: $('#progress-title'), progressAttempt: $('#progress-attempt'),
  progressSteps: $('#progress-steps'), progressNote: $('#progress-note'), progressClose: $('#progress-close'),
  composer: $('#composer'), prompt: $('#prompt'), status: $('#status'), build: $('#build'), buildLabel: $('#build-label'),
  file: $('#file'), mic: $('#mic'), attachment: $('#attachment'), thumb: $('#attachment-thumb'),
  attachmentName: $('#attachment-name'), attachmentHint: $('#attachment-hint'),
  recordingBar: $('#recording-bar'), recTime: $('#rec-time'), level: $('#level'),
  code: $('#code'), lines: $('#lines'), dirty: $('#dirty'),
  runCode: $('#run-code'), resetCode: $('#reset-code'), log: $('#log'),
  modelsDialog: $('#models-dialog'), codeModel: $('#code-model'), visionModel: $('#vision-model'),
  voiceModel: $('#voice-model'), voiceField: $('#voice-field'), cloudWarning: $('#cloud-warning'), cloudNote: $('#cloud-note'),
  arrange: $('#arrange'), tray: $('#arrange-tray'), partList: $('#part-list'),
  moveTool: $('#move-tool'), rotateTool: $('#rotate-tool'), dropPart: $('#drop-part'),
  removePart: $('#remove-part'), joinParts: $('#join-parts'),
  sketch: $('#sketch'), pad: $('#pad'), penTool: $('#pen-tool'), eraserTool: $('#eraser-tool'),
};

let project = null;
let selectedRevision = null;
let attachedImage = null;
let busy = false;

// ---------- Icons ----------

function icon(name, className = 'icon') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  svg.setAttribute('class', className);
  use.setAttribute('href', '#' + name);
  svg.append(use);
  return svg;
}

function setIcon(svg, name, spinning = false) {
  svg.querySelector('use').setAttribute('href', '#' + name);
  svg.classList.toggle('spin', spinning);
}

// ---------- Panels ----------

function setPanel(name, open) {
  document.body.dataset[name] = open ? 'open' : 'closed';
  $(`#toggle-${name}`).setAttribute('aria-pressed', String(open));
}

function togglePanel(name) {
  const open = document.body.dataset[name] !== 'open';
  if (open && narrowScreen.matches) setPanel(name === 'history' ? 'code' : 'history', false);
  setPanel(name, open);
}

function closeDrawers() {
  if (!narrowScreen.matches) return;
  setPanel('history', false);
  setPanel('code', false);
}

function fitPanelsToScreen() {
  setPanel('history', !narrowScreen.matches);
  setPanel('code', !narrowScreen.matches);
}

// ---------- 3D viewer ----------

const viewer = $('#viewer');
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100000);
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
const controls = new OrbitControls(camera, renderer.domElement);
const geometryCache = new Map();
let mesh = null;
let grid = null;
let viewToken = 0;

function setupViewer() {
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  viewer.prepend(renderer.domElement);
  controls.enableDamping = true;

  scene.add(new THREE.HemisphereLight(0xdde9ff, 0x263650, 2.4));
  const key = new THREE.DirectionalLight(0xffffff, 3.5);
  key.position.set(3, 4, 5);
  const fill = new THREE.DirectionalLight(0x82a7ff, 2);
  fill.position.set(-3, -1, 2);
  scene.add(key, fill);
  setGrid(200);
  camera.position.set(160, 120, 160);

  new ResizeObserver(resizeViewer).observe(viewer);
  resizeViewer();
  animate();
}

function setGrid(size) {
  if (grid) scene.remove(grid);
  grid = new THREE.GridHelper(size, 20, 0x465c80, 0x202b42);
  scene.add(grid);
}

function resizeViewer() {
  const width = viewer.clientWidth;
  const height = viewer.clientHeight;
  if (!width || !height) return;
  renderer.setSize(width, height, false);
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

// Artifacts never change once built, so each run's STL is downloaded and parsed once.
function loadGeometry(revision) {
  if (!geometryCache.has(revision.run_id)) {
    const url = revision.stl_url + '?v=' + encodeURIComponent(revision.run_id);
    const loading = new STLLoader().loadAsync(url).then((geometry) => {
      geometry.computeVertexNormals();
      return geometry;
    });
    loading.catch(() => geometryCache.delete(revision.run_id));
    geometryCache.set(revision.run_id, loading);
  }
  return geometryCache.get(revision.run_id);
}

function standingGeometry(source) {
  const geometry = source.clone();
  geometry.rotateX(-Math.PI / 2);
  geometry.computeBoundingBox();
  const box = geometry.boundingBox;
  const center = box.getCenter(new THREE.Vector3());
  geometry.translate(-center.x, -box.min.y, -center.z);
  return geometry;
}

function clearModel() {
  if (!mesh) return;
  scene.remove(mesh);
  mesh.geometry.dispose();
  mesh.material.dispose();
  mesh = null;
}

async function showModel(revision) {
  const token = ++viewToken;
  const spinner = setTimeout(() => { ui.viewerLoading.hidden = false; }, 150);
  try {
    const source = await loadGeometry(revision);
    if (token !== viewToken) return;
    clearModel();
    const material = new THREE.MeshStandardMaterial({ color: 0x7fa3ff, metalness: 0.18, roughness: 0.36 });
    mesh = new THREE.Mesh(standingGeometry(source), material);
    mesh.visible = !arranging;
    scene.add(mesh);
    ui.empty.hidden = true;
    if (!arranging) fitView();
  } catch {
    if (token === viewToken) setStatus('Preview failed to load', true);
  } finally {
    clearTimeout(spinner);
    if (token === viewToken) ui.viewerLoading.hidden = true;
  }
}

function fitView() {
  const target = arranging ? partsGroup : mesh;
  if (!target) return;
  const box = new THREE.Box3().setFromObject(target);
  if (box.isEmpty()) return;
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const radius = Math.max(size.x, size.y, size.z, 1);
  const distance = radius * Math.max(1, 1 / camera.aspect);
  setGrid(Math.ceil((radius * 3 + 2 * Math.max(Math.abs(center.x), Math.abs(center.z))) / 10) * 10);
  camera.near = radius / 1000;
  camera.far = distance * 100;
  camera.position.set(center.x + distance * 1.5, box.min.y + distance * 1.2, center.z + distance * 1.5);
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

// ---------- Arrange mode: place, move and join copies of parts ----------

// CadQuery is Z-up and the viewer is Y-up. Parts live in a group turned -90° about X,
// so a part's position and rotation inside it are plain CadQuery coordinates.
const partsGroup = new THREE.Group();
const gizmo = new TransformControls(camera, renderer.domElement);
const parts = [];
let arranging = false;
let selectedPart = null;
let pressStart = null;

function setupArrange() {
  partsGroup.rotation.x = -Math.PI / 2;
  partsGroup.visible = false;
  scene.add(partsGroup, gizmo.getHelper());
  gizmo.setSpace('world');
  gizmo.setTranslationSnap(1);
  gizmo.setRotationSnap(THREE.MathUtils.degToRad(15));
  gizmo.addEventListener('dragging-changed', (event) => { controls.enabled = !event.value; });
  gizmo.addEventListener('objectChange', snapPart);
}

function setArrange(on) {
  arranging = on;
  ui.arrange.setAttribute('aria-pressed', String(on));
  ui.tray.hidden = !on;
  partsGroup.visible = on;
  if (mesh) mesh.visible = !on;
  if (!on) {
    selectPart(null);
    [...parts].forEach(removePart);
    fitView();
    setStatus(selectedRevision ? `On v${selectedRevision.number}` : 'Ready');
    return;
  }
  renderPartList();
  if (selectedRevision) addPart(selectedRevision, false);
  setStatus('Arrange · add parts, tap one to move it, then join');
}

function renderPartList() {
  ui.partList.replaceChildren(...(project?.revisions || []).map((revision) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = `v${revision.number} · ${revision.instruction || 'Image prompt'}`;
    button.title = 'Add a copy of v' + revision.number;
    button.addEventListener('click', () => addPart(revision, true));
    return button;
  }));
  updateArrangeButtons();
}

async function addPart(revision, besideOthers) {
  let source;
  try {
    source = await loadGeometry(revision);
  } catch {
    setStatus(`Could not load v${revision.number}`, true);
    return;
  }
  if (!arranging) return;
  const geometry = source.clone();
  geometry.computeBoundingBox();
  const color = PART_COLORS[parts.length % PART_COLORS.length];
  const material = new THREE.MeshStandardMaterial({ color, metalness: 0.18, roughness: 0.36 });
  const part = new THREE.Mesh(geometry, material);
  part.userData.revision = revision;
  if (besideOthers && parts.length) {
    const others = new THREE.Box3().setFromObject(partsGroup);
    part.position.x = Math.ceil(others.max.x - geometry.boundingBox.min.x + 10);
  }
  parts.push(part);
  partsGroup.add(part);
  ui.empty.hidden = true;
  selectPart(part);
  fitView();
}

function removePart(part) {
  if (!part) return;
  if (part === selectedPart) selectPart(null);
  parts.splice(parts.indexOf(part), 1);
  partsGroup.remove(part);
  part.geometry.dispose();
  part.material.dispose();
  updateArrangeButtons();
}

function selectPart(part) {
  if (selectedPart) selectedPart.material.emissive.setHex(0x000000);
  selectedPart = part;
  if (part) {
    part.material.emissive.setHex(0x2a3a5c);
    gizmo.attach(part);
  } else {
    gizmo.detach();
  }
  updateArrangeButtons();
}

function setGizmoMode(mode) {
  gizmo.setMode(mode);
  ui.moveTool.setAttribute('aria-pressed', String(mode === 'translate'));
  ui.rotateTool.setAttribute('aria-pressed', String(mode === 'rotate'));
}

function snapPart() {
  // Dragging world "up" moves local z, which the world-space snap skips, so round here.
  if (gizmo.mode === 'translate') selectedPart.position.round();
}

function dropToFloor() {
  if (!selectedPart) return;
  const box = new THREE.Box3().setFromObject(selectedPart, true);
  selectedPart.position.z -= box.min.y;
}

function updateArrangeButtons() {
  ui.arrange.disabled = !project?.revisions.length;
  ui.dropPart.disabled = ui.removePart.disabled = !selectedPart;
  ui.joinParts.disabled = busy || !parts.length;
}

function pickStart(event) {
  pressStart = arranging ? { x: event.clientX, y: event.clientY, onGizmo: gizmo.axis !== null } : null;
}

function pickPart(event) {
  if (!pressStart || pressStart.onGizmo) return;
  const moved = Math.hypot(event.clientX - pressStart.x, event.clientY - pressStart.y);
  pressStart = null;
  if (moved > 5) return;
  const box = renderer.domElement.getBoundingClientRect();
  const pointer = new THREE.Vector2(
    (event.clientX - box.left) / box.width * 2 - 1,
    -(event.clientY - box.top) / box.height * 2 + 1,
  );
  const raycaster = new THREE.Raycaster();
  raycaster.setFromCamera(pointer, camera);
  selectPart(raycaster.intersectObjects(parts, false)[0]?.object || null);
}

function handleArrangeKeys(event) {
  if (!arranging || event.target.closest('input, textarea, select') || document.querySelector('dialog[open]')) return;
  const key = event.key.toLowerCase();
  if ((key === 'delete' || key === 'backspace') && selectedPart) {
    event.preventDefault();
    removePart(selectedPart);
  }
  if (key === 'g') setGizmoMode('translate');
  if (key === 'r') setGizmoMode('rotate');
  if (key === 'escape') selectPart(null);
}

// ---------- Arrange mode: joined CadQuery source ----------

function round(value, step) {
  return Math.round(value / step) * step + 0;
}

function formatNumber(value, step) {
  return String(Number(round(value, step).toFixed(2)));
}

function partFunction(revision) {
  const body = revision.code.trimEnd().split('\n').map((line) => (line.trim() ? '    ' + line : '')).join('\n');
  const returns = !/^\s*result\s*=[^=]/m.test(revision.code) && /^\s*solid\s*=[^=]/m.test(revision.code) ? 'solid' : 'result';
  return `def part_v${revision.number}():\n${body}\n    return ${returns}`;
}

function partPlacement(part, index) {
  let expression = `part_v${part.userData.revision.number}()`;
  const turns = [['z', '(0, 0, 1)'], ['y', '(0, 1, 0)'], ['x', '(1, 0, 0)']];
  turns.forEach(([axis, vector]) => {
    const degrees = formatNumber(THREE.MathUtils.radToDeg(part.rotation[axis]), 0.1);
    if (degrees !== '0') expression += `.rotate((0, 0, 0), ${vector}, ${degrees})`;
  });
  const offset = ['x', 'y', 'z'].map((axis) => formatNumber(part.position[axis], 0.01));
  if (offset.some((value) => value !== '0')) expression += `.translate((${offset.join(', ')}))`;
  return `part_${index + 1} = ${expression}`;
}

function joinedSource() {
  const byNumber = new Map(parts.map((part) => [part.userData.revision.number, part.userData.revision]));
  const revisions = [...byNumber.values()];
  const names = parts.map((_, index) => `part_${index + 1}`);
  const union = names[0] + names.slice(1).map((name) => `.union(${name})`).join('');
  return [
    'import cadquery as cq',
    ...revisions.map(partFunction),
    parts.map(partPlacement).join('\n') + '\nresult = ' + union + '\n',
  ].join('\n\n\n');
}

function joinParts() {
  if (ui.joinParts.disabled) return;
  const code = joinedSource();
  setEditor(code);
  saveSource(code, `Joined ${parts.length} part(s) as`, `Joined ${parts.length} part(s) in Arrange`);
}

// ---------- Server ----------

function errorDetail(data, status) {
  const detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
  return detail || `Request failed (${status})`;
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorDetail(data, response.status));
  return data;
}

function postJson(path, body) {
  return request(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

// Reads an NDJSON response line by line and hands each event to onEvent.
async function streamEvents(path, options, onEvent) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(errorDetail(data, response.status));
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const lines = buffer.split('\n');
    buffer = done ? '' : lines.pop();
    lines.filter((line) => line.trim()).forEach((line) => onEvent(JSON.parse(line)));
    if (done) return;
  }
}

function projectPath(suffix = '') {
  return '/projects/' + encodeURIComponent(project.project_id) + suffix;
}

function rememberProject(id) {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {}
}

function savedProjectId() {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function setProject(next) {
  project = next;
  renderHistory();
}

async function ensureProject() {
  if (project) return;
  setProject(await postJson('/projects', { name: ui.nameInput.value.trim() || 'Untitled design' }));
  rememberProject(project.project_id);
}

async function loadProject() {
  const id = savedProjectId();
  if (!id) return;
  try {
    setProject(await request('/projects/' + encodeURIComponent(id)));
  } catch {
    rememberProject(null);
    return;
  }
  ui.nameInput.value = project.name;
  if (project.revisions.length) selectRevision(project.revisions.at(-1));
}

async function loadConfig() {
  const config = await request('/config');
  ui.mic.hidden = !config.voice || !navigator.mediaDevices?.getUserMedia;
}

// ---------- Model choice ----------

let modelsCache = null;

function savedModels() {
  try {
    return JSON.parse(localStorage.getItem(MODELS_KEY)) || {};
  } catch {
    return {};
  }
}

const isCloud = (name) => name.startsWith('hf:');
const shortName = (name) => name.replace(/^hf:/, '');

function optionGroup(label, names) {
  const group = document.createElement('optgroup');
  group.label = label;
  group.append(...names.map((name) => new Option(shortName(name), name)));
  return group;
}

function fillSelect(select, names, wanted) {
  const groups = [
    optionGroup('On this machine', names.filter((name) => !isCloud(name))),
    optionGroup('Hugging Face (cloud)', names.filter(isCloud)),
  ];
  select.replaceChildren(...groups.filter((group) => group.children.length));
  if (names.includes(wanted)) select.value = wanted;
}

function renderModels(data) {
  const saved = savedModels();
  const names = data.models.map((model) => model.name);
  const vision = data.models.filter((model) => model.vision).map((model) => model.name);
  const voices = data.voice_models || [];
  fillSelect(ui.codeModel, names, saved.code || data.model);
  fillSelect(ui.visionModel, vision.length ? vision : names, saved.vision || data.vision_model);
  ui.voiceModel.replaceChildren(...voices.map((name) => new Option(name, name)));
  if (voices.includes(saved.voice || data.voice_model)) ui.voiceModel.value = saved.voice || data.voice_model;
  ui.voiceField.hidden = !voices.length;
  ui.cloudNote.hidden = data.cloud !== false;
  showModels();
}

// Renders the cached list at once, then refreshes it from the server in the background.
async function loadModels() {
  if (modelsCache) renderModels(modelsCache);
  try {
    const data = await request('/models');
    const changed = JSON.stringify(data) !== JSON.stringify(modelsCache);
    modelsCache = data;
    if (changed) renderModels(data);
  } catch (error) {
    if (!modelsCache) setStatus('Model server unreachable: ' + error.message, true);
    showModels();
  }
}

function showModels() {
  const names = [...new Set([ui.codeModel.value, ui.visionModel.value].filter(Boolean))];
  const cloud = names.some(isCloud);
  ui.cloudWarning.hidden = !cloud;
  ui.model.classList.toggle('cloud', cloud);
  setIcon(ui.model.querySelector('svg'), cloud ? 'i-cloud' : 'i-cpu');
  ui.modelLabel.textContent = names.length ? (cloud ? 'Cloud · ' : 'Local · ') + names.map(shortName).join(' + ') : 'Local model';
  ui.model.title = 'Choose models · ' + ui.modelLabel.textContent;
  ui.attachmentHint.textContent = 'Will be read by ' + (shortName(ui.visionModel.value) || 'the vision model');
}

function saveModels() {
  try {
    const choice = { code: ui.codeModel.value, vision: ui.visionModel.value, voice: ui.voiceModel.value };
    localStorage.setItem(MODELS_KEY, JSON.stringify(choice));
  } catch {}
  showModels();
}

function openModels() {
  ui.modelsDialog.showModal();
  loadModels();
}

// ---------- Status, busy state, log ----------

let busyTimer = null;

function setStatus(text, isError = false) {
  ui.status.textContent = text;
  ui.status.title = text;
  ui.status.className = isError ? 'error' : '';
}

function idleBuildLabel() {
  return selectedRevision ? 'Build next step' : 'Build first part';
}

function setBusy(stage, buttonText = 'Building') {
  busy = Boolean(stage);
  ui.build.disabled = busy;
  clearInterval(busyTimer);
  setIcon(ui.build.querySelector('svg'), busy ? 'i-loader' : 'i-send', busy);
  ui.buildLabel.textContent = idleBuildLabel();
  if (busy) {
    setStatus(stage);
    const started = Date.now();
    const tick = () => { ui.buildLabel.textContent = `${buttonText}… ${Math.round((Date.now() - started) / 1000)}s`; };
    tick();
    busyTimer = setInterval(tick, 1000);
  }
  updateEditorState();
  updateArrangeButtons();
}

const LOG_ICONS = { ok: 'i-check', fail: 'i-alert', info: 'i-info' };

function logLine(text, kind = 'info') {
  const row = document.createElement('div');
  const message = document.createElement('span');
  row.className = 'log-row ' + kind;
  message.textContent = text;
  row.append(icon(LOG_ICONS[kind] || LOG_ICONS.info), message);
  ui.log.append(row);
  ui.log.scrollTop = ui.log.scrollHeight;
}

function renderLog(revision) {
  ui.log.replaceChildren();
  if (!revision) {
    logLine('Each build is checked, rendered and verified here.');
    return;
  }
  if (revision.model === 'manual') {
    logLine('manual run · checked & rendered', 'ok');
    return;
  }
  const trace = revision.trace || [];
  if (!trace.length) logLine(`accepted after ${revision.attempts} attempt(s)`, 'ok');
  trace.forEach((problems, index) => {
    if (problems.length) logLine(`attempt ${index + 1} failed: ${problems.join('; ')}`, 'fail');
    else logLine(`attempt ${index + 1} passed · rendered & checked`, 'ok');
  });
}

function showError(error) {
  setStatus(error.message, true);
  logLine(error.message, 'fail');
}

// ---------- Progress overlay ----------

const STAGES = {
  describe: { label: 'Read drawing', icon: 'i-eye', text: 'Reading the drawing' },
  write: { label: 'Write code', icon: 'i-code', text: 'Writing code' },
  verify: { label: 'Check & render', icon: 'i-box', text: 'Checking & rendering' },
  done: { label: 'Done', icon: 'i-flag', text: 'Done' },
};
const STATE_ICONS = { active: 'i-loader', done: 'i-check', failed: 'i-alert' };
let progressTimer = null;

function stepItem(name) {
  const item = document.createElement('li');
  const dot = document.createElement('span');
  const label = document.createElement('span');
  item.className = 'step';
  item.dataset.stage = name;
  item.dataset.state = 'pending';
  dot.className = 'step-dot';
  dot.append(icon(STAGES[name].icon));
  label.className = 'step-label';
  label.textContent = STAGES[name].label;
  item.append(dot, label);
  return item;
}

function setStepState(item, state) {
  item.dataset.state = state;
  setIcon(item.querySelector('svg'), STATE_ICONS[state] || STAGES[item.dataset.stage].icon, state === 'active');
}

function setStage(name, state = 'active') {
  const items = [...ui.progressSteps.children];
  const index = items.findIndex((item) => item.dataset.stage === name);
  items.forEach((item, i) => setStepState(item, i < index ? 'done' : i === index ? state : 'pending'));
}

function openProgress(title, stageNames) {
  clearTimeout(progressTimer);
  ui.progress.className = 'progress';
  ui.progressTitle.textContent = title;
  ui.progressAttempt.textContent = '';
  ui.progressNote.textContent = '';
  ui.progressClose.hidden = true;
  ui.progressSteps.replaceChildren(...stageNames.map(stepItem));
  setStage(stageNames[0]);
  ui.progress.hidden = ui.busyBar.hidden = false;
}

function finishProgress(title) {
  setStage('done', 'done');
  ui.progressTitle.textContent = title;
  ui.progressNote.textContent = '';
  ui.progress.classList.add('success');
  ui.busyBar.hidden = true;
  progressTimer = setTimeout(hideProgress, 800);
}

function failProgress(title, message) {
  const current = ui.progressSteps.querySelector('[data-state="active"]');
  if (current) setStepState(current, 'failed');
  ui.progressTitle.textContent = title;
  ui.progressNote.textContent = message;
  ui.progressNote.title = message;
  ui.progress.classList.add('error');
  ui.progressClose.hidden = false;
  ui.busyBar.hidden = true;
}

function hideProgress() {
  clearTimeout(progressTimer);
  ui.progress.hidden = ui.busyBar.hidden = true;
}

function shorten(text, limit = 140) {
  return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
}

// ---------- History & revisions ----------

function renderHistory() {
  ui.history.replaceChildren();
  updateArrangeButtons();
  if (arranging) renderPartList();
  if (!project || !project.revisions.length) {
    const hint = document.createElement('p');
    hint.className = 'hint';
    hint.textContent = 'Builds appear here. Start with the main body, then add one feature at a time.';
    ui.history.append(hint);
    return;
  }
  project.revisions.slice().reverse().forEach((revision) => ui.history.append(revisionButton(revision)));
  markActiveRevision();
}

function revisionButton(revision) {
  const button = document.createElement('button');
  const label = document.createElement('strong');
  const text = document.createElement('span');
  button.className = 'revision';
  button.dataset.number = revision.number;
  label.textContent = 'v' + revision.number + (revision.model === 'manual' ? ' · code' : '');
  text.textContent = revision.instruction || 'Image prompt';
  button.title = revision.instruction;
  button.append(label, text);
  button.addEventListener('click', () => {
    selectRevision(revision);
    closeDrawers();
  });
  return button;
}

function markActiveRevision() {
  ui.history.querySelectorAll('.revision').forEach((button) =>
    button.classList.toggle('active', Number(button.dataset.number) === selectedRevision?.number));
}

function formatSize(size) {
  if (!size || size.length !== 3) return '';
  return size.map((value) => Math.round(value * 10) / 10).join(' × ') + ' mm';
}

function selectRevision(revision) {
  selectedRevision = revision;
  markActiveRevision();
  renderLog(revision);
  setEditor(revision.code);
  ui.title.textContent = `v${revision.number} · ${revision.instruction || 'Image prompt'}`;
  ui.title.title = revision.instruction;
  ui.size.textContent = formatSize(revision.size);
  ui.step.href = revision.step_url;
  ui.stl.href = revision.stl_url;
  ui.step.hidden = ui.stl.hidden = false;
  showModel(revision);
  if (!busy) ui.buildLabel.textContent = idleBuildLabel();
  const latest = revision.number === project.revisions.at(-1).number;
  setStatus(latest ? `On v${revision.number}` : `On v${revision.number} · the next build branches from here`);
}

function newProject() {
  if (busy) return;
  setArrange(false);
  selectedRevision = null;
  setProject(null);
  rememberProject(null);
  hideProgress();
  ui.nameInput.value = 'Untitled design';
  ui.prompt.value = '';
  clearAttachment();
  renderLog(null);
  setEditor(STARTER_CODE);
  viewToken += 1;
  ui.viewerLoading.hidden = true;
  clearModel();
  ui.title.textContent = 'No model yet';
  ui.size.textContent = '';
  ui.step.hidden = ui.stl.hidden = true;
  ui.empty.hidden = false;
  ui.buildLabel.textContent = idleBuildLabel();
  setStatus('New project ready');
}

// ---------- Building from a prompt ----------

let lastAttempt = 1;

function showBuildEvent(event) {
  if (event.event === 'retry') {
    const problems = (event.problems || []).join('; ') || 'the check failed';
    setStage('verify', 'failed');
    ui.progressNote.textContent = 'Fixing: ' + shorten(problems);
    ui.progressNote.title = problems;
    logLine(`attempt ${event.attempt} failed: ${problems}`, 'fail');
    return;
  }
  if (event.event !== 'stage' || !STAGES[event.stage]) return;
  const doing = STAGES[event.stage].text;
  setStage(event.stage);
  if (!event.attempt) {
    setStatus(doing + '…');
    logLine(doing.toLowerCase() + '…');
    return;
  }
  lastAttempt = event.attempt;
  ui.progressAttempt.textContent = `Attempt ${event.attempt} of ${event.max_attempts}`;
  setStatus(`Attempt ${event.attempt} of ${event.max_attempts} · ${doing.toLowerCase()}…`);
  logLine(`attempt ${event.attempt} · ${doing.toLowerCase()}…`);
}

async function streamBuild(form) {
  let result = null;
  await streamEvents(projectPath('/steps/stream'), { method: 'POST', body: form }, (event) => {
    if (event.event === 'error') throw new Error(event.detail || 'The build failed');
    if (event.event === 'done') result = event.project;
    else showBuildEvent(event);
  });
  if (!result) throw new Error('The build stopped before it finished');
  return result;
}

function buildForm(instruction) {
  const form = new FormData();
  form.append('instruction', instruction);
  if (selectedRevision) form.append('base_revision', selectedRevision.number);
  if (attachedImage) form.append('image', attachedImage, attachedImage.name || 'image.png');
  if (ui.codeModel.value) form.append('model', ui.codeModel.value);
  if (ui.visionModel.value) form.append('vision_model', ui.visionModel.value);
  return form;
}

async function buildStep(event) {
  event.preventDefault();
  const instruction = ui.prompt.value.trim();
  if (busy) return;
  if (!instruction && !attachedImage) {
    setStatus('Describe the part, or attach a drawing', true);
    ui.prompt.focus();
    return;
  }

  const stages = attachedImage ? ['describe', 'write', 'verify', 'done'] : ['write', 'verify', 'done'];
  lastAttempt = 1;
  ui.log.replaceChildren();
  logLine('build: ' + (instruction || 'from the attached image'));
  setBusy(STAGES[stages[0]].text + '…');
  openProgress(`Building v${(project?.revisions.length || 0) + 1}`, stages);
  try {
    await ensureProject();
    const next = await streamBuild(buildForm(instruction));
    logLine(`attempt ${lastAttempt} passed · checked & rendered`, 'ok');
    setProject(next);
    ui.prompt.value = '';
    clearAttachment();
    selectRevision(project.revisions.at(-1));
    setBusy(null);
    finishProgress(`v${selectedRevision.number} ready`);
    setStatus(`v${selectedRevision.number} built and saved`);
  } catch (error) {
    setBusy(null);
    failProgress('Build failed', error.message);
    showError(error);
  }
}

function appendToPrompt(text) {
  const current = ui.prompt.value.trim();
  ui.prompt.value = current ? current + ' ' + text : text;
  ui.prompt.focus();
}

// ---------- Code editor ----------

function isCodeChanged() {
  return ui.code.value !== (selectedRevision ? selectedRevision.code : STARTER_CODE);
}

function updateEditorState() {
  const count = Math.max(1, ui.code.value.split('\n').length);
  ui.lines.textContent = Array.from({ length: count }, (_, i) => i + 1).join('\n');
  const changed = isCodeChanged();
  ui.dirty.textContent = changed ? 'edited' : '';
  ui.runCode.disabled = busy || !ui.code.value.trim() || (!changed && Boolean(selectedRevision));
  ui.resetCode.disabled = !changed;
}

function setEditor(source) {
  ui.code.value = source;
  ui.code.scrollTop = 0;
  ui.code.scrollLeft = 0;
  ui.lines.scrollTop = 0;
  updateEditorState();
}

function runCode() {
  if (!ui.runCode.disabled) saveSource(ui.code.value, 'Code saved as');
}

async function saveSource(code, doneText, label = null) {
  setBusy('Checking & rendering…', label ? 'Joining' : 'Running');
  openProgress(label ? 'Joining parts' : 'Running your code', ['verify', 'done']);
  try {
    await ensureProject();
    const next = await postJson(projectPath('/source'), {
      code,
      base_revision: selectedRevision?.number ?? null,
      label,
    });
    if (arranging) setArrange(false);
    setProject(next);
    selectRevision(project.revisions.at(-1));
    setBusy(null);
    finishProgress(`v${selectedRevision.number} ready`);
    setStatus(`${doneText} v${selectedRevision.number}`);
  } catch (error) {
    setBusy(null);
    failProgress(label ? 'Join failed' : 'Run failed', error.message);
    showError(error);
  }
}

function handleEditorKeys(event) {
  if (event.key === 'Tab') {
    event.preventDefault();
    ui.code.setRangeText('  ', ui.code.selectionStart, ui.code.selectionEnd, 'end');
    updateEditorState();
  }
  if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
    event.preventDefault();
    runCode();
  }
}

// ---------- Image attachments ----------

function attachImage(file, name = file?.name) {
  if (!file || !IMAGE_TYPES.includes(file.type)) {
    setStatus('Use a PNG, JPEG or WebP image', true);
    return;
  }
  clearAttachment();
  attachedImage = file;
  ui.thumb.src = URL.createObjectURL(file);
  ui.attachmentName.textContent = name || 'Pasted image';
  ui.attachmentName.title = ui.attachmentName.textContent;
  ui.attachment.hidden = false;
  setStatus('Image attached · add a note or press Build');
}

function clearAttachment() {
  if (ui.thumb.src) URL.revokeObjectURL(ui.thumb.src);
  ui.thumb.removeAttribute('src');
  ui.attachment.hidden = true;
  ui.file.value = '';
  attachedImage = null;
}

function imageFromPaste(event) {
  const item = [...event.clipboardData.items].find((entry) => entry.type.startsWith('image/'));
  if (!item) return;
  event.preventDefault();
  attachImage(item.getAsFile(), 'Pasted image');
}

function imageFromDrop(event) {
  event.preventDefault();
  ui.composer.classList.remove('dragging');
  const file = event.dataTransfer.files[0];
  if (file) attachImage(file);
}

// ---------- Sketch pad ----------

const pen = ui.pad.getContext('2d');
const undoStack = [];
let drawing = false;
let erasing = false;

function clearPad() {
  pen.fillStyle = '#fff';
  pen.fillRect(0, 0, ui.pad.width, ui.pad.height);
}

function setTool(eraser) {
  erasing = eraser;
  ui.penTool.setAttribute('aria-pressed', String(!eraser));
  ui.eraserTool.setAttribute('aria-pressed', String(eraser));
}

function padPoint(event) {
  const box = ui.pad.getBoundingClientRect();
  return [
    (event.clientX - box.left) * ui.pad.width / box.width,
    (event.clientY - box.top) * ui.pad.height / box.height,
  ];
}

function startStroke(event) {
  undoStack.push(pen.getImageData(0, 0, ui.pad.width, ui.pad.height));
  if (undoStack.length > 30) undoStack.shift();
  drawing = true;
  ui.pad.setPointerCapture(event.pointerId);
  pen.strokeStyle = erasing ? '#fff' : '#000';
  pen.lineWidth = erasing ? 22 : 3;
  pen.lineCap = pen.lineJoin = 'round';
  pen.beginPath();
  pen.moveTo(...padPoint(event));
  pen.lineTo(...padPoint(event));
  pen.stroke();
}

function continueStroke(event) {
  if (!drawing) return;
  pen.lineTo(...padPoint(event));
  pen.stroke();
}

function undoStroke() {
  const previous = undoStack.pop();
  if (previous) pen.putImageData(previous, 0, 0);
}

function openSketch() {
  undoStack.length = 0;
  clearPad();
  setTool(false);
  ui.sketch.showModal();
}

function useSketch() {
  ui.sketch.close();
  ui.prompt.focus();
  ui.pad.toBlob((blob) => attachImage(new File([blob], 'sketch.png', { type: 'image/png' }), 'Sketch'), 'image/png');
}

// ---------- Voice ----------

let recorder = null;
let recordingTimer = null;
let sendRecording = true;
let audioContext = null;
let meterFrame = 0;

async function startRecording() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    setStatus('Microphone blocked or missing: ' + error.message, true);
    return;
  }
  const chunks = [];
  recorder = new MediaRecorder(stream);
  recorder.ondataavailable = (event) => chunks.push(event.data);
  recorder.onstop = () => {
    stopMeter();
    stream.getTracks().forEach((track) => track.stop());
    const type = recorder.mimeType || 'audio/webm';
    recorder = null;
    showRecording(false);
    if (sendRecording) transcribe(new Blob(chunks, { type }));
    else setStatus('Recording cancelled');
  };
  sendRecording = true;
  recorder.start();
  showRecording(true);
  startMeter(stream);
}

function stopRecording(send) {
  if (!recorder) return;
  sendRecording = send;
  recorder.stop();
}

// Shows the recent input loudness as a row of bars that scrolls from right to left.
function startMeter(stream) {
  try {
    audioContext = new AudioContext();
  } catch {
    return;
  }
  const analyser = audioContext.createAnalyser();
  analyser.fftSize = 512;
  audioContext.createMediaStreamSource(stream).connect(analyser);
  const samples = new Uint8Array(analyser.fftSize);
  const bars = [...ui.level.children];
  const levels = bars.map(() => 0);
  const draw = () => {
    analyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (const sample of samples) sum += ((sample - 128) / 128) ** 2;
    levels.shift();
    levels.push(Math.min(1, Math.sqrt(sum / samples.length) * 4));
    bars.forEach((bar, i) => { bar.style.transform = `scaleY(${0.12 + levels[i] * 0.88})`; });
    meterFrame = requestAnimationFrame(draw);
  };
  draw();
}

function stopMeter() {
  cancelAnimationFrame(meterFrame);
  audioContext?.close().catch(() => {});
  audioContext = null;
  [...ui.level.children].forEach((bar) => { bar.style.transform = ''; });
}

function showRecording(on) {
  ui.recordingBar.hidden = !on;
  ui.mic.classList.toggle('recording', on);
  setIcon(ui.mic.querySelector('svg'), on ? 'i-stop' : 'i-mic');
  clearInterval(recordingTimer);
  if (!on) return;
  const started = Date.now();
  ui.recTime.textContent = '0:00';
  recordingTimer = setInterval(() => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    ui.recTime.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    if (seconds >= MAX_RECORDING_SECONDS) stopRecording(true);
  }, 250);
  setStatus('Listening… speak your prompt');
}

async function transcribe(audio) {
  ui.mic.disabled = true;
  setIcon(ui.mic.querySelector('svg'), 'i-loader', true);
  setStatus('Transcribing…');
  const form = new FormData();
  form.append('audio', audio, 'speech.webm');
  if (ui.voiceModel.value) form.append('model', ui.voiceModel.value);
  try {
    const { text } = await request('/transcribe', { method: 'POST', body: form });
    if (text) appendToPrompt(text.trim());
    setStatus(text ? 'Voice added to the prompt · edit it or press Build' : 'No speech heard, try again', !text);
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    ui.mic.disabled = false;
    setIcon(ui.mic.querySelector('svg'), 'i-mic');
  }
}

function setupMeter() {
  ui.level.replaceChildren(...Array.from({ length: METER_BARS }, () => document.createElement('i')));
}

// ---------- Wiring ----------

function bindEvents() {
  $('#toggle-history').addEventListener('click', () => togglePanel('history'));
  $('#toggle-code').addEventListener('click', () => togglePanel('code'));
  document.querySelectorAll('[data-close]').forEach((button) =>
    button.addEventListener('click', () => setPanel(button.dataset.close, false)));
  $('.backdrop').addEventListener('click', closeDrawers);
  narrowScreen.addEventListener('change', fitPanelsToScreen);
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !ui.sketch.open && !ui.modelsDialog.open) closeDrawers();
  });

  ui.composer.addEventListener('submit', buildStep);
  ui.composer.addEventListener('paste', imageFromPaste);
  ui.composer.addEventListener('dragover', (event) => {
    event.preventDefault();
    ui.composer.classList.add('dragging');
  });
  ui.composer.addEventListener('dragleave', () => ui.composer.classList.remove('dragging'));
  ui.composer.addEventListener('drop', imageFromDrop);
  ui.prompt.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      ui.composer.requestSubmit();
    }
  });
  document.querySelectorAll('.chip').forEach((chip) => chip.addEventListener('click', () => {
    ui.prompt.value = chip.dataset.prompt;
    ui.prompt.focus();
    ui.prompt.setSelectionRange(ui.prompt.value.length, ui.prompt.value.length);
  }));

  $('#attach').addEventListener('click', () => ui.file.click());
  ui.file.addEventListener('change', () => attachImage(ui.file.files[0]));
  $('#remove-attachment').addEventListener('click', clearAttachment);
  ui.progressClose.addEventListener('click', hideProgress);

  ui.mic.addEventListener('click', () => (recorder ? stopRecording(true) : startRecording()));
  $('#stop-recording').addEventListener('click', () => stopRecording(true));
  $('#cancel-recording').addEventListener('click', () => stopRecording(false));

  $('#open-sketch').addEventListener('click', openSketch);
  $('#close-sketch').addEventListener('click', () => ui.sketch.close());
  $('#cancel-sketch').addEventListener('click', () => ui.sketch.close());
  ui.sketch.addEventListener('click', (event) => { if (event.target === ui.sketch) ui.sketch.close(); });
  $('#use-sketch').addEventListener('click', useSketch);
  $('#clear-sketch').addEventListener('click', () => {
    undoStack.push(pen.getImageData(0, 0, ui.pad.width, ui.pad.height));
    clearPad();
  });
  $('#undo-sketch').addEventListener('click', undoStroke);
  ui.penTool.addEventListener('click', () => setTool(false));
  ui.eraserTool.addEventListener('click', () => setTool(true));
  ui.pad.addEventListener('pointerdown', startStroke);
  ui.pad.addEventListener('pointermove', continueStroke);
  ui.pad.addEventListener('pointerup', () => { drawing = false; });
  ui.pad.addEventListener('pointercancel', () => { drawing = false; });

  ui.model.addEventListener('click', openModels);
  ui.codeModel.addEventListener('change', saveModels);
  ui.visionModel.addEventListener('change', saveModels);
  ui.voiceModel.addEventListener('change', saveModels);
  $('#close-models').addEventListener('click', () => ui.modelsDialog.close());
  $('#done-models').addEventListener('click', () => ui.modelsDialog.close());
  ui.modelsDialog.addEventListener('click', (event) => { if (event.target === ui.modelsDialog) ui.modelsDialog.close(); });

  $('#fit').addEventListener('click', fitView);
  ui.arrange.addEventListener('click', () => setArrange(!arranging));
  ui.moveTool.addEventListener('click', () => setGizmoMode('translate'));
  ui.rotateTool.addEventListener('click', () => setGizmoMode('rotate'));
  ui.dropPart.addEventListener('click', dropToFloor);
  ui.removePart.addEventListener('click', () => removePart(selectedPart));
  ui.joinParts.addEventListener('click', joinParts);
  renderer.domElement.addEventListener('pointerdown', pickStart);
  renderer.domElement.addEventListener('pointerup', pickPart);
  document.addEventListener('keydown', handleArrangeKeys);
  $('#new-project').addEventListener('click', newProject);
  $('#copy').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(ui.code.value);
      setStatus('Code copied');
    } catch {
      setStatus('Copy is blocked by the browser', true);
    }
  });
  ui.code.addEventListener('input', updateEditorState);
  ui.code.addEventListener('scroll', () => { ui.lines.scrollTop = ui.code.scrollTop; });
  ui.code.addEventListener('keydown', handleEditorKeys);
  ui.runCode.addEventListener('click', runCode);
  ui.resetCode.addEventListener('click', () => {
    setEditor(selectedRevision?.code || STARTER_CODE);
    setStatus('Code reset');
  });
}

fitPanelsToScreen();
setupViewer();
setupArrange();
setupMeter();
bindEvents();
renderHistory();
renderLog(null);
setEditor(STARTER_CODE);
loadConfig().catch(() => {});
loadModels();
loadProject().catch(() => setStatus('Could not restore the last project', true));
