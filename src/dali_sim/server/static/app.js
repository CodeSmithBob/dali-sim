/* dali-sim web UI. Plain JavaScript, no build step, no external requests. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
const SVGNS = "http://www.w3.org/2000/svg";
const svg = (tag, attrs = {}) => {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
};
const h = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
};

let state = null;
let currentLine = 1;
try { currentLine = parseInt(localStorage.getItem("dali-sim-line") || "1", 10) || 1; } catch (e) { /* storage may be blocked */ }
let lineSwitched = true;
let catalog = null;
let selected = null; // {kind: 'gear'|'device', name}
const monitor = { rows: [], paused: false, selected: null, max: 3000 };

/* ---------------- networking ---------------- */
async function api(path, body) {
  if (body && typeof body === "object" && !Array.isArray(body) && body.line === undefined) body = { ...body, line: currentLine };
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok || j.ok === false) throw new Error(j.error || r.statusText);
  return j;
}
function toast(text, err = false) {
  const t = h("div", { class: "toast" + (err ? " err" : "") }, text);
  document.body.append(t);
  setTimeout(() => t.remove(), err ? 6000 : 2500);
}
const act = (action, args = {}) => api("/api/act", { action, ...args }).catch(e => toast(e.message, true));

let ws = null;
/* ---------------- lines ---------------- */
function selectLine(n) {
  if (n === currentLine) return;
  currentLine = n;
  lineSwitched = true;
  selected = null;
  $("#details").innerHTML = "<h2>Details</h2><p class='muted'>Select a lamp or a device.</p>";
  try { localStorage.setItem("dali-sim-line", String(n)); } catch (e) { /* ignore */ }
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "select", line: n }));
}
function renderLines(m) {
  const bar = $("#linebar");
  const lines = m.lines || [];
  bar.hidden = lines.length < 2;
  if (bar.hidden) return;
  $("#buildingname").textContent = m.building || "";
  const host = $("#lines");
  if (host.children.length !== lines.length) {
    host.textContent = "";
    for (const l of lines) {
      const b = h("button", { class: "line", role: "tab", "data-line": l.line },
        h("span", { class: "lname" }), h("span", { class: "lstats" }));
      b.addEventListener("click", () => selectLine(l.line));
      host.append(b);
    }
  }
  lines.forEach((l, i) => {
    const b = host.children[i];
    b.classList.toggle("active", l.line === currentLine);
    b.setAttribute("aria-selected", l.line === currentLine ? "true" : "false");
    $(".lname", b).textContent = `Line ${l.line} · ${l.name}`;
    $(".lstats", b).textContent = `${l.gear} gear · ${l.devices} devices · ${l.lamps_on} on · ${l.power_w} W` + (l.powered ? "" : " · NO BUS POWER") + (l.errors ? ` · ${l.errors} errors` : "");
  });
}

function connect() {
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws?line=" + currentLine);
  ws.onopen = () => { $("#wsdot").classList.add("on"); $("#wsstate").textContent = "live"; };
  ws.onclose = () => { $("#wsdot").classList.remove("on"); $("#wsstate").textContent = "offline"; setTimeout(connect, 1500); };
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.type === "hello") {
      if (m.line !== currentLine) { currentLine = m.line; lineSwitched = true; }
      monitor.rows = []; addRecords(m.history); renderLines(m); setState(m.state);
    }
    else if (m.type === "state") {
      if (m.line !== currentLine) return;  // a frame sent before a line switch
      addRecords(m.records); renderLines(m); setState(m.state);
    }
    else if (m.type === "progress") { if (!m.line || m.line === currentLine) onProgress(m); }
    else if (m.type === "job") onJob(m);
    else if (m.type === "error") toast(m.error, true);
  };
}
function wsAct(action, args) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: "act", action, line: currentLine, ...args }));
  else act(action, args);
}

/* ---------------- state ---------------- */
function setState(s) {
  const first = !state || state.name !== s.name || lineSwitched;
  lineSwitched = false;
  state = s;
  $("#simname").textContent = "· " + s.name;
  $("#sim-desc").textContent = s.description || "";
  $("#simtime").textContent = s.time.toFixed(1);
  $("#st-fwd").textContent = s.bus.stats.forward;
  $("#st-bwd").textContent = s.bus.stats.backward;
  $("#st-ev").textContent = s.bus.stats.events;
  $("#st-col").textContent = s.bus.stats.collisions + s.bus.stats.framing_errors;
  $("#st-power").textContent = s.bus.power_w;
  $("#buspower").checked = s.bus.powered;
  if (document.activeElement !== $("#daylight")) {
    $("#daylight").value = s.world.outdoor_lux;
  }
  $("#daylight-v").textContent = s.world.outdoor_lux;
  $("#people").checked = s.world.people;
  if (s.speed && document.activeElement !== $("#speed")) {
    const sel = $("#speed");
    if (![...sel.options].some(o => +o.value === s.speed)) sel.append(h("option", { value: s.speed }, s.speed + "x"));
    sel.value = String(s.speed);
  }
  if (first) { $("#plan").innerHTML = ""; $("#devcards").innerHTML = ""; planCache.clear(); cardCache.clear(); }
  renderPlan();
  renderGearMap($("#gearmap"));
  renderGearMap($("#cm-map"));
  renderUnaddressed();
  renderDevices();
  renderDetails();
  renderBridges();
}

const gearByName = n => state.gear.find(g => g.name === n);
const devByName = n => state.devices.find(d => d.name === n);

function lampColour(g) {
  if (g.dt8 && g.dt8.rgb) return g.dt8.rgb;
  return "#ffd86b";
}
function brightness(g) { return g.percent > 0 ? Math.cbrt(g.percent / 100) : 0; }

/* ---------------- floor plan ---------------- */
const planCache = new Map();
function layout() {
  // Returns {rooms:[{name,x,y,w,h,gear:[...],devices:[...]}], width, height}
  const rooms = state.world.rooms.map(r => ({ ...r, gearList: [], devList: [] }));
  const byName = new Map(rooms.map(r => [r.name, r]));
  const loose = { name: "", gearList: [], devList: [] };
  for (const g of state.gear) (byName.get(g.meta.room) || loose).gearList.push(g);
  for (const d of state.devices) if (d.instances.length) (byName.get(d.meta.room) || loose).devList.push(d);
  if (loose.gearList.length || loose.devList.length) {
    const n = loose.gearList.length;
    const cols = Math.min(16, Math.max(4, Math.ceil(Math.sqrt(n * 2))));
    const rowsN = Math.ceil(n / cols) + (loose.devList.length ? Math.ceil(loose.devList.length / cols) : 0);
    const y0 = rooms.length ? Math.max(...rooms.map(r => r.y + r.h)) + 0.4 : 0;
    Object.assign(loose, { name: rooms.length ? "Other units" : state.name, x: 0, y: y0, w: Math.max(cols * 0.8 + 0.4, 4), h: rowsN * 0.8 + 0.8, loose: true });
    rooms.push(loose);
  }
  const width = Math.max(...rooms.map(r => r.x + r.w), 4) + 0.2;
  const height = Math.max(...rooms.map(r => r.y + r.h), 3) + 0.2;
  return { rooms, width, height };
}

function renderPlan() {
  const plan = $("#plan");
  const L = layout();
  plan.setAttribute("viewBox", `-0.2 -0.2 ${L.width + 0.2} ${L.height + 0.2}`);
  if (!plan.querySelector("defs")) {
    const defs = svg("defs");
    const f = svg("filter", { id: "glow", x: "-100%", y: "-100%", width: "300%", height: "300%" });
    f.append(svg("feGaussianBlur", { stdDeviation: "0.18" }));
    defs.append(f);
    // layers: rooms below glows below lamps and sensors
    plan.append(defs, svg("g", { id: "l-rooms" }), svg("g", { id: "l-halos" }), svg("g", { id: "l-items" }));
  }
  const seen = new Set();
  for (const r of L.rooms) {
    const key = "room:" + r.name;
    seen.add(key);
    let g = planCache.get(key);
    if (!g) {
      g = svg("g", { class: "room" });
      g.append(svg("rect", { rx: 0.08 }), svg("text"));
      $("#l-rooms", plan).append(g);
      planCache.set(key, g);
    }
    const [rect, text] = g.children;
    rect.setAttribute("x", r.x); rect.setAttribute("y", r.y); rect.setAttribute("width", r.w); rect.setAttribute("height", r.h);
    text.setAttribute("x", r.x + 0.12); text.setAttribute("y", r.y + 0.3);
    text.textContent = r.name + (r.lux !== undefined ? `  ·  ${r.lux} lx` : "") + (r.people ? `  ·  ${r.people} people` : "");
    // place lamps in a grid inside the room
    const n = r.gearList.length;
    const cols = r.loose ? Math.max(1, Math.floor((r.w - 0.4) / 0.8)) : Math.max(1, Math.ceil(Math.sqrt(n * r.w / r.h)));
    const rows = Math.max(1, Math.ceil(n / cols));
    const cw = (r.w - 0.4) / cols, ch = r.loose ? 0.8 : (r.h - 1.2) / rows;
    r.gearList.forEach((gear, i) => {
      const cx = r.x + 0.2 + cw * (i % cols + 0.5);
      const cy = r.y + 0.6 + ch * (Math.floor(i / cols) + 0.5);
      seen.add("lamp:" + gear.name);
      drawLamp(plan, gear, cx, cy, Math.min(0.28, cw * 0.32, ch * 0.4));
    });
    // devices along the bottom edge
    const dy = r.loose ? r.y + 0.6 + rows * ch + 0.15 : r.y + r.h - 0.42;
    let dx = r.x + 0.2;
    for (const d of r.devList) {
      for (const inst of d.instances) {
        const key2 = "sensor:" + d.name + ":" + inst.number;
        seen.add(key2);
        drawSensor(plan, d, inst, dx, dy);
        dx += 0.78;
        if (dx > r.x + r.w - 0.7) { dx = r.x + 0.2; }
      }
    }
  }
  for (const [k, el] of planCache) if (!seen.has(k)) { if (el._halo) el._halo.remove(); el.remove(); planCache.delete(k); }
}

function drawLamp(plan, g, cx, cy, r) {
  const key = "lamp:" + g.name;
  let el = planCache.get(key);
  if (!el) {
    el = svg("g", { class: "lamp", tabindex: 0 });
    el.append(svg("circle", { class: "bulb" }), svg("path", { class: "fail" }), svg("circle", { class: "ident" }), svg("text"));
    el.addEventListener("click", () => select("gear", g.name));
    el.addEventListener("keydown", e => { if (e.key === "Enter") select("gear", g.name); });
    el._halo = svg("circle", { class: "halo", filter: "url(#glow)" });
    $("#l-halos", plan).append(el._halo);
    $("#l-items", plan).append(el);
    planCache.set(key, el);
  }
  const halo = el._halo;
  const [bulb, fail, ident, text] = el.children;
  const b = brightness(g);
  const col = lampColour(g);
  halo.setAttribute("cx", cx); halo.setAttribute("cy", cy); halo.setAttribute("r", r * (1.2 + 1.6 * b));
  halo.setAttribute("fill", col); halo.setAttribute("opacity", (0.75 * b).toFixed(3));
  bulb.setAttribute("cx", cx); bulb.setAttribute("cy", cy); bulb.setAttribute("r", r);
  bulb.setAttribute("fill", b > 0 ? col : "var(--lamp-off)");
  bulb.setAttribute("fill-opacity", b > 0 ? (0.35 + 0.65 * b).toFixed(3) : 1);
  bulb.setAttribute("stroke-dasharray", g.mains ? "none" : "0.06 0.05");
  const bad = g.lamp_failure || g.gear_failure;
  fail.setAttribute("d", bad ? `M${cx - r * .6} ${cy - r * .6}L${cx + r * .6} ${cy + r * .6}M${cx + r * .6} ${cy - r * .6}L${cx - r * .6} ${cy + r * .6}` : "");
  ident.setAttribute("cx", cx); ident.setAttribute("cy", cy); ident.setAttribute("r", r * 1.35);
  ident.style.display = g.identifying ? "" : "none";
  text.setAttribute("x", cx); text.setAttribute("y", cy + r + 0.18);
  text.textContent = (g.short_address === null ? "--" : "A" + g.short_address) + (g.dt1 && g.dt1.emergency ? " 🔋" : "");
  el.classList.toggle("sel", selected && selected.name === g.name);
  el.setAttribute("aria-label", `${g.name}, ${g.percent.toFixed(1)} percent`);
}

const SENSOR_ICON = { "push-button": "BTN", "absolute-input": "SLD", "occupancy": "PIR", "light-sensor": "LUX" };
function drawSensor(plan, d, inst, x, y) {
  const key = "sensor:" + d.name + ":" + inst.number;
  let el = planCache.get(key);
  if (!el) {
    el = svg("g", { class: "sensor", tabindex: 0 });
    el.append(svg("rect", { width: 0.7, height: 0.32, rx: 0.06 }), svg("text"));
    el.addEventListener("click", () => select("device", d.name));
    if (inst.kind === "push-button") {
      el.addEventListener("pointerdown", e => { e.preventDefault(); wsAct("press", { device: d.name, instance: inst.number }); });
      const up = () => wsAct("release", { device: d.name, instance: inst.number });
      el.addEventListener("pointerup", up);
      el.addEventListener("pointerleave", e => { if (e.buttons) up(); });
    } else if (inst.kind === "occupancy") {
      el.addEventListener("dblclick", () => {
        const cur = devByName(d.name).instances[inst.number];
        wsAct("motion", { device: d.name, instance: inst.number, moving: !cur.movement });
      });
    }
    $("#l-items", plan).append(el);
    planCache.set(key, el);
  }
  const [rect, text] = el.children;
  rect.setAttribute("x", x); rect.setAttribute("y", y);
  text.setAttribute("x", x + 0.35); text.setAttribute("y", y + 0.22);
  let label = SENSOR_ICON[inst.kind] || "INP";
  let active = false;
  if (inst.kind === "push-button") active = inst.pressed;
  if (inst.kind === "occupancy") { active = inst.occupied; label += inst.movement ? " ~" : ""; }
  if (inst.kind === "light-sensor") label = Math.round(inst.lux) + " lx";
  if (inst.kind === "absolute-input") label = "SLD " + inst.value;
  text.textContent = label;
  el.classList.toggle("active", active);
}

/* ---------------- address maps ---------------- */
function renderGearMap(el) {
  if (!el.children.length) {
    for (let a = 0; a < 64; a++) {
      const c = h("div", { class: "cell empty", title: "A" + a }, h("div", { class: "fill" }), h("span", {}, "A" + a));
      c.addEventListener("click", () => { const g = c.dataset.name; if (g) select("gear", g); });
      el.append(c);
    }
  }
  const owners = new Map();
  for (const g of state.gear) if (g.short_address !== null) {
    if (!owners.has(g.short_address)) owners.set(g.short_address, []);
    owners.get(g.short_address).push(g);
  }
  [...el.children].forEach((c, a) => {
    const list = owners.get(a) || [];
    const g = list[0];
    c.classList.toggle("empty", !g);
    c.classList.toggle("conflict", list.length > 1);
    c.classList.toggle("sel", !!(g && selected && selected.name === g.name));
    const fill = c.firstChild;
    if (g) {
      c.dataset.name = g.name;
      fill.style.height = (brightness(g) * 100).toFixed(1) + "%";
      fill.style.background = lampColour(g);
      c.title = `A${a}: ${g.name}, level ${g.level} (${g.percent}%)` + (list.length > 1 ? ` — ADDRESS CONFLICT (${list.length} units)` : "");
    } else {
      delete c.dataset.name;
      fill.style.height = "0";
      c.title = "A" + a + ": free";
    }
  });
}

function renderUnaddressed() {
  const el = $("#unaddressed");
  const list = state.gear.filter(g => g.short_address === null);
  el.textContent = "";
  if (!list.length) { el.append(h("span", { class: "muted" }, "none")); return; }
  for (const g of list.slice(0, 80)) el.append(h("span", { class: "badge", title: "random address " + g.random_address }, g.name));
  if (list.length > 80) el.append(h("span", { class: "muted" }, `… ${list.length - 80} more`));
}

/* ---------------- device cards ---------------- */
const cardCache = new Map();
function renderDevices() {
  const host = $("#devcards");
  const seen = new Set();
  for (const d of state.devices) {
    seen.add(d.name);
    let card = cardCache.get(d.name);
    if (!card) {
      card = h("div", { class: "card" });
      card.append(h("h4", {}, h("span", { class: "name" }), h("span", { class: "addr badge" })));
      for (const inst of d.instances) card.append(instanceControl(d, inst));
      if (d.kind === "app-controller") card.append(h("div", { class: "muted rules", style: "font-size:12px" }));
      card.addEventListener("click", e => { if (e.target === card || e.target.closest("h4")) select("device", d.name); });
      host.append(card);
      cardCache.set(d.name, card);
    }
    $(".name", card).textContent = d.name;
    $(".addr", card).textContent = d.short_address === null ? "no address" : "D" + d.short_address;
    card.classList.toggle("sel", !!(selected && selected.name === d.name));
    d.instances.forEach(inst => updateInstance(card, inst));
    const rules = $(".rules", card);
    if (rules) rules.textContent = `${d.rules.length} rules, ${d.rules.reduce((s, r) => s + r.hits, 0)} triggered` +
      (d.daylight.length ? ` · daylight: ${d.daylight.map(x => `${x.target} ${x.measured_lux}/${x.setpoint_lux} lx`).join(", ")}` : "");
  }
  for (const [k, el] of cardCache) if (!seen.has(k)) { el.remove(); cardCache.delete(k); }
}

function instanceControl(d, inst) {
  const row = h("div", { class: "inst", "data-inst": inst.number });
  const lbl = h("span", { class: "lbl" });
  row.append(lbl);
  const args = { device: d.name, instance: inst.number };
  if (inst.kind === "push-button") {
    const b = h("button", { class: "pb", "aria-label": "Push button " + inst.number }, "Push");
    b.addEventListener("pointerdown", e => { e.preventDefault(); wsAct("press", args); });
    b.addEventListener("pointerup", () => wsAct("release", args));
    b.addEventListener("pointerleave", e => { if (e.buttons) wsAct("release", args); });
    b.addEventListener("keydown", e => { if ((e.key === " " || e.key === "Enter") && !e.repeat) wsAct("press", args); });
    b.addEventListener("keyup", e => { if (e.key === " " || e.key === "Enter") wsAct("release", args); });
    row.append(b);
  } else if (inst.kind === "occupancy") {
    row.append(h("button", { onclick: () => wsAct("motion", { ...args, moving: true }) }, "Motion"),
      h("button", { onclick: () => wsAct("motion", { ...args, moving: false }) }, "Still"));
  } else if (inst.kind === "light-sensor") {
    const r = h("input", { type: "range", min: 0, max: inst.max_lux, step: 10, "aria-label": "Illuminance" });
    r.addEventListener("input", () => wsAct("set_lux", { ...args, lux: +r.value }));
    row.append(r);
  } else if (inst.kind === "absolute-input") {
    const r = h("input", { type: "range", min: 0, max: inst.max, step: 1, "aria-label": "Position" });
    r.addEventListener("input", () => wsAct("set_value", { ...args, value: +r.value }));
    row.append(r);
  }
  row.append(h("span", { class: "badge ev" }));
  return row;
}

function updateInstance(card, inst) {
  const row = $(`.inst[data-inst="${inst.number}"]`, card);
  if (!row) return;
  const typeName = { "push-button": "Button", "occupancy": "Occupancy", "light-sensor": "Light", "absolute-input": "Slider" }[inst.kind] || "Input";
  let text = `I${inst.number} ${typeName}${inst.label ? " · " + inst.label : ""}`;
  if (inst.kind === "occupancy") text += inst.occupied ? " · occupied" : " · vacant";
  if (inst.kind === "light-sensor") text += ` · ${Math.round(inst.lux)} lx`;
  $(".lbl", row).textContent = text;
  const pb = $(".pb", row);
  if (pb) pb.classList.toggle("down", inst.pressed);
  const range = $("input[type=range]", row);
  if (range && document.activeElement !== range) range.value = inst.kind === "light-sensor" ? inst.lux : inst.value;
  $(".ev", row).textContent = inst.last_event ? `${inst.events_sent} · ${inst.last_event}` : "no events";
}

/* ---------------- details panel ---------------- */
function select(kind, name) { selected = { kind, name }; renderDetails(true); setState(state); }

function renderDetails(force = false) {
  const el = $("#details");
  if (!selected) return;
  if (!force && el.contains(document.activeElement) && document.activeElement.tagName === "INPUT") {
    // keep sliders steady while the user drags them
    updateDetailValues();
    return;
  }
  const u = selected.kind === "gear" ? gearByName(selected.name) : devByName(selected.name);
  if (!u) return;
  el.textContent = "";
  el.append(h("h2", {}, u.name));
  if (selected.kind === "gear") gearDetails(el, u); else deviceDetails(el, u);
}

function updateDetailValues() {
  const u = selected && gearByName(selected.name);
  const v = $("#d-level-v");
  if (u && v) v.textContent = `${u.level} (${u.percent}%)`;
}

const kv = pairs => h("dl", { class: "kv" }, pairs.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))]));

function gearDetails(el, g) {
  const addr = g.short_address === null ? null : g.short_address;
  const target = addr === null ? null : "A" + addr;
  const status = g.status;
  const bits = ["gear failure", "lamp failure", "lamp on", "limit error", "fade running", "reset state", "no short address", "power cycle seen"]
    .filter((_, i) => status & (1 << i));
  el.append(kv([
    ["Short address", addr === null ? "none" : "A" + addr],
    ["Random address", g.random_address],
    ["Device types", g.device_types.length ? g.device_types.map(t => "DT" + t).join(", ") : "none"],
    ["Actual level", `${g.level} (${g.percent}%)${g.fading ? " fading" : ""}`],
    ["Min / max", `${g.min_level} / ${g.max_level}`],
    ["Power on / failure", `${g.power_on_level} / ${g.system_failure_level}`],
    ["Fade time / rate", `${g.fade_time} / ${g.fade_rate}`],
    ["Groups", g.groups.length ? g.groups.map(x => "G" + x).join(" ") : "none"],
    ["Scenes", g.scenes.map((v, i) => v === 255 ? null : `S${i}=${v}`).filter(Boolean).join(" ") || "none"],
    ["Status", bits.join(", ") || "0"],
    ["Power", g.power_w + " W"],
  ]));
  if (g.dt8) el.append(h("h3", {}, "Colour (DT8)"), kv([
    ["Type", g.dt8.colour_type], ["Tc", g.dt8.tc_kelvin ? g.dt8.tc_kelvin + " K" : "-"],
    ["xy", `${g.dt8.x}, ${g.dt8.y}`], ["RGBWAF", g.dt8.rgbwaf.join(" ") || "-"],
  ]));
  if (g.dt1) el.append(h("h3", {}, "Emergency (DT1)"), kv([
    ["Battery", g.dt1.battery + " %"], ["Mode", g.dt1.emergency ? (g.dt1.rest ? "rest" : "emergency") : "normal"],
    ["Function test", g.dt1.function_test ? "running" : g.dt1.ft_failed ? "failed" : "-"],
    ["Duration test", g.dt1.duration_test ? "running" : g.dt1.dt_result_min ? g.dt1.dt_result_min + " min" : "-"],
  ]));
  if (target) {
    el.append(h("h3", {}, "Control through the bus"));
    const lv = h("input", { type: "range", min: 0, max: 254, value: g.level, "aria-label": "Level (DAPC)" });
    lv.addEventListener("change", () => sendCmd({ kind: "gear", dapc: true, target, opcode: +lv.value }));
    el.append(h("div", { class: "row" }, lv, h("span", { id: "d-level-v", class: "mono" }, `${g.level}`)));
    el.append(h("div", { class: "row", style: "margin-top:6px" },
      h("button", { onclick: () => sendCmd({ kind: "gear", target, opcode: 0x05 }) }, "Max"),
      h("button", { onclick: () => sendCmd({ kind: "gear", target, opcode: 0x06 }) }, "Min"),
      h("button", { onclick: () => sendCmd({ kind: "gear", target, opcode: 0x00 }) }, "Off"),
      h("button", { onclick: () => sendCmd({ kind: "gear", target, opcode: 0x25 }) }, "Identify"),
      h("button", { onclick: () => sendCmd({ kind: "gear", target, opcode: 0x90 }) }, "Query status")));
    if (g.dt8 && g.dt8.colour_type === "tc") {
      const tc = h("input", { type: "range", min: 2700, max: 6500, step: 100, value: g.dt8.tc_kelvin || 4000, "aria-label": "Colour temperature" });
      tc.addEventListener("change", () => {
        const m = Math.round(1e6 / +tc.value);
        sendCmd({ kind: "gear", target, opcode: 0xE7, dt: 8, dtr0: m & 255, dtr1: m >> 8 }).then(() => sendCmd({ kind: "gear", target, opcode: 0xE2, dt: 8 }));
      });
      el.append(h("div", { class: "row", style: "margin-top:6px" }, "Tc", tc));
    }
    if (g.dt8 && g.dt8.colour_type === "rgbwaf") {
      const col = h("input", { type: "color", value: g.dt8.rgb, "aria-label": "Colour" });
      col.addEventListener("change", () => {
        const v = col.value; const r = parseInt(v.slice(1, 3), 16), gg = parseInt(v.slice(3, 5), 16), b = parseInt(v.slice(5, 7), 16);
        const s = x => Math.round(x / 255 * 254);
        sendCmd({ kind: "gear", target, opcode: 0xEB, dt: 8, dtr0: s(r), dtr1: s(gg), dtr2: s(b) }).then(() => sendCmd({ kind: "gear", target, opcode: 0xE2, dt: 8 }));
      });
      el.append(h("div", { class: "row", style: "margin-top:6px" }, "RGB", col));
    }
    if (g.dt1) {
      el.append(h("div", { class: "row", style: "margin-top:6px" },
        h("button", { onclick: () => sendCmd({ kind: "gear", target, opcode: 0xE3, dt: 1 }) }, "Function test"),
        h("button", { onclick: () => sendCmd({ kind: "gear", target, opcode: 0xE4, dt: 1 }) }, "Duration test"),
        h("button", { onclick: () => sendCmd({ kind: "gear", target, opcode: 0xE5, dt: 1 }) }, "Stop test")));
    }
  }
  el.append(h("h3", {}, "Physical world (fault injection)"));
  el.append(h("div", { class: "row" },
    h("label", { class: "inline" }, cb(g.mains, v => act("mains", { unit: g.name, on: v })), "mains"),
    h("label", { class: "inline" }, cb(g.lamp_failure, v => act("lamp_failure", { unit: g.name, on: v })), "lamp failure"),
    h("label", { class: "inline" }, cb(g.gear_failure, v => act("gear_failure", { unit: g.name, on: v })), "gear failure")));
}
function cb(checked, fn) {
  const c = h("input", { type: "checkbox" });
  c.checked = checked;
  c.addEventListener("change", () => fn(c.checked));
  return c;
}

function deviceDetails(el, d) {
  const bits = ["input device error", "quiescent", "no short address", "app controller active", "app controller error", "power cycle seen", "reset state"]
    .filter((_, i) => d.status & (1 << i));
  el.append(kv([
    ["Short address", d.short_address === null ? "none" : "D" + d.short_address],
    ["Random address", d.random_address],
    ["Device groups", d.groups.length ? d.groups.map(x => "DG" + x).join(" ") : "none"],
    ["Instances", d.instances.length],
    ["Status", bits.join(", ") || "0"],
  ]));
  for (const i of d.instances) {
    el.append(h("h3", {}, `Instance ${i.number}: ${i.kind}`), kv([
      ["Enabled", i.enabled], ["Event scheme", i.scheme], ["Priority", i.priority],
      ["Primary group", i.primary_group === null ? "none" : i.primary_group], ["Input value", i.value],
      ["Events sent", i.events_sent], ["Last event", i.last_event || "-"],
    ]));
  }
  if (d.kind === "app-controller") {
    el.append(h("h3", {}, "Rules"));
    el.append(h("div", { class: "out" }, d.rules.map(r => `${JSON.stringify(r.when)} → ${JSON.stringify(r.do)}  (${r.hits})`).join("\n")));
  }
  el.append(h("h3", {}, "Physical world"));
  el.append(h("label", { class: "inline" }, cb(d.mains, v => act("mains", { unit: d.name, on: v })), "mains / supply"));
}

/* ---------------- monitor ---------------- */
function addRecords(recs) {
  if (!recs || !recs.length || monitor.paused) return;
  monitor.rows.push(...recs);
  if (monitor.rows.length > monitor.max) monitor.rows.splice(0, monitor.rows.length - monitor.max);
  if ($("#view-monitor").classList.contains("active")) renderMonitor();
}
function kindGroup(k) { return k === "forward" ? "forward" : k === "backward" ? "backward" : k === "event" ? "event" : "other"; }
function monitorRows() {
  const f = $("#mon-filter").value.trim().toLowerCase();
  const show = { forward: $("#mon-forward").checked, backward: $("#mon-backward").checked, event: $("#mon-event").checked, other: $("#mon-other").checked };
  return monitor.rows.filter(r => show[kindGroup(r.kind)] && (!f || `${r.text} ${r.hex} ${r.source}`.toLowerCase().includes(f)));
}
function renderMonitor() {
  const rows = monitorRows();
  const body = $("#mon-body");
  const wrap = body.closest(".mon-wrap");
  const atBottom = wrap.scrollTop + wrap.clientHeight >= wrap.scrollHeight - 30;
  const last = rows.slice(-600);
  body.textContent = "";
  for (const r of last) {
    const tr = h("tr", { class: monitor.selected === r.seq ? "sel" : "" },
      h("td", {}, r.seq), h("td", { class: "mono" }, r.t.toFixed(4)), h("td", {}, r.source),
      h("td", { class: "k-" + r.kind }, r.kind), h("td", {}, r.bits || ""), h("td", { class: "mono" }, r.hex || ""),
      h("td", { class: "text" }, r.text), h("td", { class: "mono" }, r.duration_ms || ""));
    tr.addEventListener("click", () => { monitor.selected = r.seq; drawWave(r); renderMonitor(); });
    body.append(tr);
  }
  $("#mon-count").textContent = `${rows.length} records`;
  if (atBottom) wrap.scrollTop = wrap.scrollHeight;
}
function drawWave(r) {
  const el = $("#wave");
  el.textContent = "";
  if (!r.hex) return;
  const bytes = r.hex.split(" ").map(x => parseInt(x, 16));
  const bits = [];
  for (const b of bytes) for (let i = 7; i >= 0; i--) bits.push((b >> i) & 1);
  // half-bit levels: 1 = idle/high, 0 = low; logical 1 = low then high
  const levels = [1, 1, 0, 1];
  for (const b of bits) levels.push(...(b ? [0, 1] : [1, 0]));
  levels.push(1, 1, 1, 1);
  const W = el.clientWidth || 900, H = 150, n = levels.length, dx = W / n;
  const y = l => (l ? 30 : 95);
  let d = `M0 ${y(levels[0])}`;
  levels.forEach((l, i) => { d += `L${i * dx} ${y(l)}L${(i + 1) * dx} ${y(l)}`; });
  for (let i = 2; i < n - 4; i += 2) el.append(svg("line", { class: "grid", x1: i * dx, x2: i * dx, y1: 20, y2: 105 }));
  el.append(svg("path", { d, class: "hi" }));
  const label = (x, t) => { const e = svg("text", { x, y: 125, "text-anchor": "middle" }); e.textContent = t; el.append(e); };
  label(3 * dx, "S");
  bits.forEach((b, i) => label((4 + i * 2 + 1) * dx, b));
  bytes.forEach((b, i) => { const e = svg("text", { x: (4 + i * 16 + 8) * dx, y: 14, "text-anchor": "middle" }); e.textContent = "0x" + b.toString(16).toUpperCase().padStart(2, "0"); el.append(e); });
  const t = svg("text", { x: W - 4, y: 145, "text-anchor": "end" });
  t.textContent = `${bits.length} bits · Te = 416.7 µs · ${r.duration_ms} ms incl. stop`;
  el.append(t);
}

/* ---------------- console ---------------- */
async function sendCmd(body) {
  body.via = body.via || $("#c-via").value;
  try {
    const r = await api("/api/command", body);
    showReply(r);
    return r;
  } catch (e) { toast(e.message, true); }
}
function showReply(r) {
  const v = r.value;
  let s = `frames: ${r.frames ? r.frames.join(" | ") : ""}\nresult: ${r.status}`;
  if (v !== null && v !== undefined) s += `  value=${v} (0x${v.toString(16).toUpperCase().padStart(2, "0")}, 0b${v.toString(2).padStart(8, "0")})`;
  if (r.responders && r.responders.length) s += `\nanswered by: ${r.responders.join(", ")}`;
  $("#c-out").textContent = s + "\n\n" + $("#c-out").textContent.slice(0, 4000);
}
function fillCommands() {
  if (!catalog) return;
  const kind = $("#c-kind").value, dt = $("#c-dt").value;
  const sel = $("#c-cmd");
  sel.textContent = "";
  let rows = [];
  if (kind === "gear") rows = dt ? catalog.dt[dt] : catalog.gear;
  else if (kind === "gear_special") rows = catalog.gear_special;
  else if (kind === "device") rows = $("#c-inst").value.trim() === "device" ? catalog.device : catalog.instance;
  else if (kind === "device_special") rows = catalog.device_special;
  for (const c of rows || []) {
    const tag = [c.answer ? "query" : "", c.twice ? "twice" : "", c.dali2 ? "DALI-2" : "", c.legacy ? "DALI-1 only" : ""].filter(Boolean).join(", ");
    sel.append(h("option", { value: c.opcode }, `0x${c.opcode.toString(16).toUpperCase().padStart(2, "0")}  ${c.name}${tag ? "  (" + tag + ")" : ""}`));
  }
  const dis = (id, on) => { $(id).disabled = !on; };
  dis("#c-cmd", kind !== "dapc");
  dis("#c-dt", kind === "gear");
  dis("#c-inst", kind === "device");
  dis("#c-target", kind === "gear" || kind === "dapc" || kind === "device");
  $("#c-hint").textContent = kind === "dapc" ? "Level 0..254, 255 = stop fading" : kind.endsWith("special") ? "Data byte goes in Level / data" : "";
}
async function consoleSend() {
  const kind = $("#c-kind").value;
  const body = { kind: kind === "dapc" ? "gear" : kind, target: $("#c-target").value, via: $("#c-via").value };
  if (kind === "dapc") { body.dapc = true; body.opcode = +$("#c-level").value; }
  else body.opcode = +$("#c-cmd").value;
  if (kind.endsWith("special")) body.data = +$("#c-level").value;
  if (kind === "gear" && $("#c-dt").value) body.dt = +$("#c-dt").value;
  if (kind === "device") body.instance = $("#c-inst").value.trim();
  for (const r of ["dtr0", "dtr1", "dtr2"]) { const v = $("#c-" + r).value; if (v !== "") body[r] = +v; }
  await sendCmd(body);
}
async function rawSend() {
  const hex = $("#c-raw").value.replace(/\s+/g, "");
  const frames = $("#c-raw-twice").checked ? [hex, hex] : [hex];
  try {
    const r = await api("/api/send", { frames, via: $("#c-via").value });
    showReply({ ...r, frames });
  } catch (e) { toast(e.message, true); }
}

/* ---------------- commissioning / jobs ---------------- */
let tbCount = 0;
function onProgress(m) {
  if (m.phase === "search") {
    const span = 0x1000000;
    $("#cm-range").style.left = (m.low / span * 100) + "%";
    $("#cm-range").style.width = Math.max(0.3, (m.high - m.low + 1) / span * 100) + "%";
    $("#cm-range-l").textContent = `${m.low.toString(16).toUpperCase().padStart(6, "0")} … ${m.high.toString(16).toUpperCase().padStart(6, "0")}`;
    $("#cm-found").textContent = m.found;
  } else if (m.phase === "programmed") {
    $("#cm-found").textContent = m.found;
    cmLog(`A${m.short_address} ← random ${m.random_address}${m.verified ? "" : "  (verify failed!)"}`);
    const cell = $("#cm-map").children[m.short_address];
    if (cell) { cell.classList.remove("flash"); void cell.offsetWidth; cell.classList.add("flash"); }
  } else if (m.phase === "scan-gear" || m.phase === "scan-devices") {
    $("#cm-range-l").textContent = `${m.phase === "scan-gear" ? "gear" : "devices"}: address ${m.address}`;
    $("#cm-range").style.left = "0"; $("#cm-range").style.width = ((m.address + 1) / 64 * 100) + "%";
  } else if (m.phase === "test") {
    tbCount++;
    const row = h("tr", {}, h("td", { class: m.passed ? "pass" : "fail" }, m.passed ? "pass" : "FAIL"), h("td", {}, m.target), h("td", {}, m.test), h("td", { class: "muted" }, m.detail));
    $("#tb-body").append(row);
    const p = $("#tb-pass"), f = $("#tb-fail");
    if (m.passed) p.textContent = +p.textContent + 1; else f.textContent = +f.textContent + 1;
    $("#tb-prog").style.width = Math.min(100, tbCount / 3) + "%";
  } else if (m.phase === "error") cmLog("error: " + m.message);
  if (state) $("#cm-time").textContent = state.time.toFixed(1);
}
function onJob(m) {
  $("#cm-state").textContent = "";
  if (m.error) { toast(`${m.name} failed: ${m.error}`, true); return; }
  if (m.cancelled) { toast(`${m.name} cancelled`); return; }
  if (m.name === "commissioning") { cmLog(`done: ${m.result.length} units addressed`); $("#cm-range-l").textContent = "done"; toast(`Commissioning done: ${m.result.length} units`); }
  if (m.name === "scan") {
    const g = Object.keys(m.result.gear), d = Object.keys(m.result.devices);
    $("#cm-scan-out").textContent = `gear (${g.length}): ${g.map(a => "A" + a).join(" ")}\ndevices (${d.length}): ${d.map(a => "D" + a).join(" ")}\n\n` + JSON.stringify(m.result, null, 1).slice(0, 6000);
    $("#cm-range-l").textContent = "scan done";
  }
  if (m.name === "testbench") { $("#tb-prog").style.width = "100%"; toast(`Test bench: ${m.result.passed} passed, ${m.result.failed} failed`, m.result.failed > 0); }
}
function cmLog(t) { const o = $("#cm-log"); o.textContent = t + "\n" + o.textContent.slice(0, 5000); }
async function startJob(path, body, label) {
  try { await api(path, body); $("#cm-state").textContent = label + " running…"; }
  catch (e) { toast(e.message, true); }
}
function parseAddrs(text) {
  text = text.trim();
  if (!text || text === "all") return null;
  const out = [];
  for (const part of text.split(",")) {
    const [a, b] = part.split("-").map(x => parseInt(x, 10));
    for (let i = a; i <= (isNaN(b) ? a : b); i++) out.push(i);
  }
  return out;
}

/* ---------------- hardware ---------------- */
function renderBridges() {
  const host = $("#hw-list");
  const list = state.bridges || [];
  const vias = ["virtual", ...list.map(b => b.adapter)];
  for (const id of ["#c-via", "#cm-via", "#tb-via"]) {
    const sel = $(id);
    if (sel.options.length !== vias.length) {
      const cur = sel.value;
      sel.textContent = "";
      for (const v of vias) sel.append(h("option", { value: v }, v === "virtual" ? "virtual bus" : "hardware: " + v));
      sel.value = vias.includes(cur) ? cur : "virtual";
    }
  }
  host.textContent = "";
  if (!list.length) { host.append(h("p", { class: "muted" }, "No hardware bridge is running. The simulator works fully virtual.")); return; }
  for (const b of list) {
    host.append(h("div", { class: "card" }, h("h4", {}, b.adapter, h("span", { class: "badge " + (b.connected ? "ok" : "bad") }, b.connected ? "connected" : "not connected")),
      kv([["Mode", b.mode], ["24-bit frames", b.forward24], ["Sniffs bus", b.sniff], ["Sends answers", b.send_backward], ["Answers sent", b.answered], ["Could not answer", b.unanswerable]]),
      b.error ? h("p", { class: "fail" }, b.error) : null,
      ...(b.warnings || []).map(w => h("p", { class: "muted" }, w)),
      b.notes ? h("p", { class: "muted" }, b.notes) : null));
  }
}

/* ---------------- wiring ---------------- */
function showView(v) {
  $$("#tabs button").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  $$(".view").forEach(s => s.classList.toggle("active", s.id === "view-" + v));
  if (v === "monitor") renderMonitor();
  try { localStorage.setItem("dali-sim-view", v); } catch (e) { /* storage may be blocked */ }
}

async function init() {
  $$("#tabs button").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));
  try { const v = localStorage.getItem("dali-sim-view"); if (v && $("#view-" + v)) showView(v); } catch (e) { /* ignore */ }
  try { const t = localStorage.getItem("dali-sim-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) { /* ignore */ }
  $("#theme").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("dali-sim-theme", document.documentElement.dataset.theme); } catch (e) { /* ignore */ }
  });
  $("#speed").addEventListener("change", e => act("speed", { value: +e.target.value }));
  $("#buspower").addEventListener("change", e => act("bus_power", { on: e.target.checked }));
  $("#daylight").addEventListener("input", e => wsAct("outdoor_lux", { lux: +e.target.value }));
  $("#people").addEventListener("change", e => act("people", { enabled: e.target.checked }));
  $("#scenario").addEventListener("change", async e => {
    try { await api("/api/load", { example: e.target.value }); selected = null; $("#details").innerHTML = "<h2>Details</h2><p class='muted'>Select a lamp or a device.</p>"; toast("Loaded " + e.target.value); }
    catch (err) { toast(err.message, true); }
  });
  $("#mon-pause").addEventListener("click", e => { monitor.paused = !monitor.paused; e.target.textContent = monitor.paused ? "Resume" : "Pause"; });
  $("#mon-clear").addEventListener("click", () => { monitor.rows = []; renderMonitor(); });
  $("#mon-csv").addEventListener("click", () => {
    const lines = ["seq,time_s,source,kind,bits,hex,decoded,duration_ms", ...monitorRows().map(r => [r.seq, r.t, r.source, r.kind, r.bits || "", r.hex || "", `"${(r.text || "").replace(/"/g, '""')}"`, r.duration_ms].join(","))];
    const a = h("a", { href: URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" })), download: "dali-bus.csv" });
    a.click();
  });
  for (const id of ["#mon-filter", "#mon-forward", "#mon-backward", "#mon-event", "#mon-other"]) $(id).addEventListener("input", renderMonitor);
  $("#c-kind").addEventListener("change", fillCommands);
  $("#c-dt").addEventListener("change", fillCommands);
  $("#c-inst").addEventListener("change", fillCommands);
  $("#c-send").addEventListener("click", consoleSend);
  $("#c-raw-send").addEventListener("click", rawSend);
  $$("[data-quick]").forEach(b => b.addEventListener("click", () => {
    const q = b.dataset.quick;
    if (q === "on") sendCmd({ kind: "gear", target: "BC", opcode: 0x05 });
    if (q === "off") sendCmd({ kind: "gear", target: "BC", opcode: 0x00 });
    if (q === "query") sendCmd({ kind: "gear", target: "BC", opcode: 0x91 });
    if (q === "identify") sendCmd({ kind: "gear", target: "BC", opcode: 0x25 });
  }));
  $("#cm-gear").addEventListener("click", () => startJob("/api/commission", { only_new: true, via: $("#cm-via").value }, "commissioning"));
  $("#cm-gear-all").addEventListener("click", () => { if (confirm("Give every control gear a new short address?")) startJob("/api/commission", { only_new: false, via: $("#cm-via").value }, "commissioning"); });
  $("#cm-dev").addEventListener("click", () => startJob("/api/commission", { devices: true, only_new: true, via: $("#cm-via").value }, "commissioning"));
  $("#cm-scan").addEventListener("click", () => startJob("/api/scan", { via: $("#cm-via").value }, "scan"));
  $("#cm-clear").addEventListener("click", () => act("clear_addresses"));
  $("#cm-cancel").addEventListener("click", () => api("/api/job/cancel", {}));
  $("#tb-run").addEventListener("click", () => {
    $("#tb-body").textContent = ""; $("#tb-pass").textContent = "0"; $("#tb-fail").textContent = "0"; tbCount = 0; $("#tb-prog").style.width = "0";
    startJob("/api/testbench", { suite: $("#tb-suite").value, addresses: parseAddrs($("#tb-addr").value), via: $("#tb-via").value }, "test bench");
  });
  try {
    catalog = await api("/api/commands");
    fillCommands();
    const ex = await api("/api/examples");
    const sel = $("#scenario");
    sel.append(h("option", { value: "" }, "(current)"));
    for (const e of ex) sel.append(h("option", { value: e }, e));
  } catch (e) { toast(e.message, true); }
  connect();
}
init();
