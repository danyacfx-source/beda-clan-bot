const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ids = ["ov_status","ov_uptime","ov_ping","ov_mem","ov_guild","ov_members","ov_online","ov_channels","ov_botname","ov_about","spark_lat","spark_mem","spark_online","mon_last","srv_channels","srv_roles","role_select","srv_members","role_member","role_result","mod_target","mod_member","mod_actions","mod_reason","mod_timeout_min","warns_total","warn_search","warns_box","gv_channel","gv_prize","gv_winners","gv_minutes","gv_mindays","gv_active","gv_finished","sch_channel","sch_at","sch_content","sch_title","sch_desc","sch_color","sch_upcoming","sch_done","backup_info","backup_config","settings-groups","files_grid","logs_box","toasts","sec-overview","sec-server","sec-moderation","sec-giveaways","sec-embed","sec-scheduler","sec-settings","sec-backup","test_result","test_content","test_channel","msg_id","f_content","f_title","f_desc","f_color","f_color_picker","f_author_name","f_author_icon","f_author_url","f_footer_text","f_footer_icon","f_image","f_thumb","btn_send","btn_edit","btn_clear","btn_load","colsw","preview","btn_editor","fields","upload_input","channels-grid"];

function makeEl(name) {
  const classSet = new Set();
  return {
    id: name, value: "", textContent: "", innerHTML: "", placeholder: "", type: "text", className: "", dataset: {},
    checked: false, style: {}, files: [], src: "", onclick: null, onchange: null, oninput: null, onkeyup: null, title: "",
    children: [],
    classList: {
      _set: classSet,
      add(c) { classSet.add(c); }, remove(c) { classSet.delete(c); },
      toggle(c, f) { if (f === undefined) { classSet.has(c) ? classSet.delete(c) : classSet.add(c); } else { f ? classSet.add(c) : classSet.delete(c); } },
      contains(c) { return classSet.has(c); },
    },
    appendChild(c) { this.children.push(c); return c; },
    append(...ns) { Array.prototype.forEach.call(ns, (n) => this.children.push(n)); },
    addEventListener() {}, removeEventListener() {}, setAttribute() {}, getAttribute() { return null; }, focus() {}, remove() {}, click() {},
    get value() { return this._v ?? ""; }, set value(v) { this._v = v; },
    get textContent() { return this._t ?? ""; }, set textContent(v) { this._t = String(v); },
    get innerHTML() { return this._h ?? ""; }, set innerHTML(v) { this._h = String(v); },
  };
}

const registry = {};
ids.forEach((id) => (registry[id] = makeEl(id)));
const elCounters = {};
const documentStub = {
  getElementById(id) { return registry[id] || (registry[id] = makeEl(id)); },
  querySelectorAll() { return []; },
  createElement(tagName) { const n = "el_" + tagName + "_" + ((elCounters[tagName] = (elCounters[tagName] || 0) + 1)); return makeEl(n); },
  createTextNode(t) { return { text: t }; },
  body: { dataset: {}, style: { setProperty() {} }, appendChild() {}, append() {} },
  title: "", hidden: false, addEventListener() {},
};

const storageData = new Map();
const localStorageStub = {
  getItem(k) { return storageData.has(k) ? storageData.get(k) : null; },
  setItem(k, v) { storageData.set(k, String(v)); },
  removeItem(k) { storageData.delete(k); },
};

const calls = [];
const sandbox = {
  document: documentStub,
  localStorage: localStorageStub,
  navigator: { clipboard: { writeText: async () => {} } },
  fetch: async (url, opts) => {
    calls.push({ url: String(url), method: (opts && opts.method) || "GET" });
    return { status: 401, ok: false, json: async () => ({}) };
  },
  setInterval, clearInterval, setTimeout, clearTimeout,
  console, confirm: () => true, prompt: () => "",
  encodeURIComponent, decodeURIComponent,
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  requestAnimationFrame: (fn) => setTimeout(fn, 0),
  URL, Blob, FormData, FileReader: class {},
  location: { reload() {} },
  tag: () => makeEl("tag" + Math.random()),
  api: async (u) => ({ status: 200, data: {} }),
  $: (id) => registry[id] || makeEl("detached_" + id),
  escapeHtml: (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])),
  relTime: () => "x",
  bootInit: () => {},
};
sandbox.window = sandbox, (sandbox.window.self = sandbox);

vm.createContext(sandbox);

const panelSrc = fs.readFileSync(path.resolve(__dirname, "../../app/core/webpanel/panel.js"), "utf8");
vm.runInContext(panelSrc, sandbox, { filename: "panel.js" });

console.log("OK: panel.js initializes in the DOM smoke harness");
process.exit(0);
