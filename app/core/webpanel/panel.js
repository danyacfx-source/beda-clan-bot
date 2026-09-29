let TOKEN = "__PANEL_TOKEN__" || localStorage.getItem("panel-token") || "";
let CSRF_TOKEN = localStorage.getItem("panel-csrf") || "";
const PANEL_LOGIN = "__PANEL_LOGIN__" === "1";
const PANEL_OAUTH = "__PANEL_OAUTH__" === "1";
const oauthQuery = typeof URLSearchParams === "function"
  ? new URLSearchParams(location.search)
  : { get: () => null };
if (oauthQuery.get("oauth_token")) {
  TOKEN = oauthQuery.get("oauth_token");
  CSRF_TOKEN = oauthQuery.get("oauth_csrf") || "";
  localStorage.setItem("panel-token", TOKEN);
  if (CSRF_TOKEN) localStorage.setItem("panel-csrf", CSRF_TOKEN);
  history.replaceState({}, document.title, location.pathname);
}
const COLORS = {blurple: 0x5865f2, green: 0x23a55a, red: 0xf23f43, yellow: 0xf0b232, dark: 0x111214, grey: 0x96989d, orange: 0xf2780d, teal: 0x1abc9c, pink: 0xeb459e};
const hex6 = /^#?([0-9a-f]{6})$/i;
const ACCENTS = ["#5865f2", "#23a55a", "#f23f43", "#1abc9c", "#eb459e", "#f2780d"];
const TITLES = {overview: "Обзор", server: "Сервер", moderation: "Модерация", giveaways: "Розыгрыши", tickets: "Тикеты", automod: "Автомод", polls: "Опросы", birthdays: "Дни рождения", tempvoice: "Голосовые", embed: "Эмбеды", scheduler: "Планировщик", settings: "Настройки", test: "Тест", files: "Файлы", logs: "Логи", audit: "Логи Discord", stats: "Статистика", backup: "Бэкап"};
const SECTION_CONTEXT = {
  overview: "Состояние сервера и главные действия в одном месте.",
  server: "Участники, каналы и голосовая активность сервера.",
  moderation: "Действия модераторов и история нарушений.",
  giveaways: "Активные и завершённые розыгрыши сообщества.",
  tickets: "Обращения участников и их история.",
  automod: "Автоматическая защита, правила и быстрый lockdown.",
  polls: "Опросы, голоса и результаты.",
  birthdays: "Календарь поздравлений для участников.",
  tempvoice: "Активные временные голосовые комнаты.",
  embed: "Создание сообщений и эмбедов с предпросмотром.",
  scheduler: "Планирование будущих публикаций.",
  settings: "Каналы и рабочие настройки сервера.",
  test: "Проверка отправки сообщений от имени бота.",
  files: "Изображения, доступные для оформления сообщений.",
  logs: "Технические события и ошибки в реальном времени.",
  audit: "События Discord: сообщения, участники и модерация.",
  stats: "Аналитика активности и динамика сообщества.",
  backup: "Экспорт данных и состояние резервных копий.",
};
const SETTINGS_GROUPS = [
  { title: "🧾 Логи аудита", cols: [["log_channel_id", "Общий лог"], ["member_log_channel_id", "Лог участников"], ["message_log_channel_id", "Лог сообщений"], ["voice_log_channel_id", "Лог голосовых"], ["mod_log_channel_id", "Лог модерации"], ["bot_log_channel_id", "Лог бота"]] },
  { title: "🎟️ Тикеты", cols: [["ticket_category_id", "Категория тикетов"]] },
];
const MODULE_LABELS = {
  birthdays: "Дни рождения", temp_voices: "Темп-голосовые", logs: "Логи (исключения)",
  automod: "Автомод", server_stats: "Статистика сервера", rules_gate: "Правила", ram_report: "Отчёт об ОЗУ",
};

let mode = "webhook";
let embeds = [newEmbed()];
let btnRows = [[]];
let settingsLoaded = false;
let settingsData = null;
let modulesData = null;
let logsTimer = null;
let auditTimer = null;
let overviewTimer = null;
let analyticsSocket = null;
let analyticsReconnectTimer = null;
let commandIndex = 0;

const $ = (id) => document.getElementById(id);

function tag(t, cls, html) {
  const el = document.createElement(t);
  if (cls) el.className = cls;
  if (html !== undefined) el.innerHTML = html;
  return el;
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function toast(msg, ok) {
  const t = tag("div", "toast " + (ok ? "ok" : "bad"), escapeHtml(msg));
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), 4200);
}

async function api(url, body, method) {
  const requestMethod = method || (body ? "POST" : "GET");
  const opts = { method: requestMethod, headers: { "X-Panel-Token": TOKEN } };
  if (requestMethod !== "GET" && CSRF_TOKEN) opts.headers["X-Panel-CSRF"] = CSRF_TOKEN;
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  try {
    const r = await fetch(url, opts);
    const data = await r.json().catch(() => ({}));
    if (r.status === 401) {
      if (PANEL_LOGIN) showLogin();
      else if (TOKEN) location.reload();
    }
    return { status: r.status, data };
  } catch (e) {
    return { status: 0, data: {} };
  }
}

async function doLogin() {
  const r = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password: $("login_pw").value }),
  });
  const d = await r.json().catch(() => ({}));
  if (d && d.token) {
    TOKEN = d.token;
    CSRF_TOKEN = d.csrf || "";
    localStorage.setItem("panel-token", d.token);
    if (CSRF_TOKEN) localStorage.setItem("panel-csrf", CSRF_TOKEN);
    $("login_pw").value = "";
    hideLogin();
    bootInit();
    toast("✅ Добро пожаловать", true);
    return true;
  }
  toast("❌ Неверный пароль", false);
  return false;
}

function showLogin() {
  $("login").style.display = "flex";
  if ($("login_pw")) $("login_pw").focus();
}
function hideLogin() {
  $("login").style.display = "none";
}

async function logout() {
  await api("/api/logout", null, "POST");
  localStorage.removeItem("panel-token");
  localStorage.removeItem("panel-csrf");
  location.reload();
}

async function uploadFile(file, targetId, cb) {
  if (!file) return;
  if (!/\.(png|jpe?g|gif|webp)$/i.test(file.name)) return toast("Формат не поддерживается: " + file.name, false);
  if (file.size > 8 * 1024 * 1024) return toast("Файл больше 8 МБ", false);
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/upload", {
    method: "POST",
    headers: { "X-Panel-Token": TOKEN, ...(CSRF_TOKEN ? { "X-Panel-CSRF": CSRF_TOKEN } : {}) },
    body: fd,
  });
  const d = await r.json().catch(() => ({}));
  if (r.status === 401) {
    if (PANEL_LOGIN) showLogin(); else if (TOKEN) location.reload();
  }
  if (d && d.ok && d.url) { $(targetId).value = d.url; if (cb) cb(d.url); else renderPreview(); toast("✅ Картинка загружена", true); }
  else toast(d.error ? "❌ " + d.error : "❌ Ошибка загрузки картинки", false);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("✅ Скопировано", true);
  } catch (e) {
    toast("❌ Не удалось скопировать", false);
  }
}

/* --- тема --- */
let theme = localStorage.getItem("panel-theme") || "dark";
let accent = localStorage.getItem("panel-accent") || ACCENTS[0];
function applyTheme() {
  document.body.dataset.theme = theme;
  document.body.style.setProperty("--accent", accent);
  document.querySelectorAll(".seg-btn").forEach((b) => b.classList.toggle("active", b.dataset.theme === theme));
  document.querySelectorAll(".accent-dot").forEach((d) => d.classList.toggle("active", d.dataset.color === accent));
}
function buildAccents() {
  const box = $("accents");
  box.innerHTML = "";
  ACCENTS.forEach((c) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = "accent-dot"; b.dataset.color = c;
    b.style.background = c;
    b.onclick = () => { accent = c; localStorage.setItem("panel-accent", c); applyTheme(); };
    box.appendChild(b);
  });
}
document.querySelectorAll(".seg-btn").forEach((b) => {
  b.addEventListener("click", () => { theme = b.dataset.theme; localStorage.setItem("panel-theme", theme); applyTheme(); });
});

/* --- навигация --- */
document.querySelectorAll(".nav-item").forEach((b) => {
  b.addEventListener("click", () => switchSection(b.dataset.section));
});
const mobileMenu = $("mobile-menu");
const sidebarScrim = $("sidebar-scrim");
const navSearch = $("nav-search");
const searchTrigger = $("search-trigger");
const paletteBackdrop = $("palette-backdrop");
const commandSearch = $("command-search");
const commandList = $("command-list");

function filterNavigation() {
  const query = (navSearch ? navSearch.value : "").trim().toLocaleLowerCase("ru");
  let visible = 0;
  document.querySelectorAll(".nav-item").forEach((item) => {
    const match = !query || item.textContent.toLocaleLowerCase("ru").includes(query);
    item.style.display = match ? "" : "none";
    if (match) visible += 1;
  });
  document.querySelectorAll(".nav-label").forEach((label) => {
    let next = label.nextElementSibling;
    let groupVisible = false;
    while (next && !next.classList.contains("nav-label") && !next.classList.contains("nav-empty")) {
      if (next.classList.contains("nav-item") && next.style.display !== "none") groupVisible = true;
      next = next.nextElementSibling;
    }
    label.style.display = groupVisible ? "" : "none";
  });
  if ($("nav-empty")) $("nav-empty").style.display = visible ? "none" : "block";
}

function commandItems() {
  return [...document.querySelectorAll(".nav-item")].map((item) => {
    const section = item.dataset.section;
    return {
      section,
      title: TITLES[section] || section,
      icon: item.querySelector("span")?.textContent || "•",
      description: SECTION_CONTEXT[section] || "Открыть раздел панели.",
    };
  });
}

function renderCommandPalette() {
  if (!commandList || !commandSearch) return;
  const query = commandSearch.value.trim().toLocaleLowerCase("ru");
  const entries = commandItems().filter((item) => (`${item.title} ${item.description}`).toLocaleLowerCase("ru").includes(query));
  commandIndex = Math.max(0, Math.min(commandIndex, Math.max(0, entries.length - 1)));
  commandList.innerHTML = "";
  if (!entries.length) {
    commandList.innerHTML = '<div class="command-empty">Ничего не найдено</div>';
    return;
  }
  entries.forEach((item, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "command-item" + (index === commandIndex ? " selected" : "");
    button.innerHTML = `<span class="cmd-icon">${escapeHtml(item.icon)}</span><span><b>${escapeHtml(item.title)}</b><small>${escapeHtml(item.description)}</small></span><kbd>↵</kbd>`;
    button.addEventListener("click", () => { closeCommandPalette(); switchSection(item.section); });
    commandList.appendChild(button);
  });
}

function openCommandPalette() {
  if (!paletteBackdrop || !commandSearch) return;
  closeMobileNav();
  commandIndex = 0;
  commandSearch.value = "";
  paletteBackdrop.classList.add("open");
  paletteBackdrop.setAttribute("aria-hidden", "false");
  renderCommandPalette();
  commandSearch.focus();
}

function closeCommandPalette() {
  if (!paletteBackdrop) return;
  paletteBackdrop.classList.remove("open");
  paletteBackdrop.setAttribute("aria-hidden", "true");
}

function focusNavigationSearch() {
  openCommandPalette();
}
if (navSearch) {
  navSearch.addEventListener("input", filterNavigation);
  navSearch.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      const first = [...document.querySelectorAll(".nav-item")].find((item) => item.style.display !== "none");
      if (first) switchSection(first.dataset.section);
    }
  });
}
if (searchTrigger) searchTrigger.addEventListener("click", focusNavigationSearch);
if (paletteBackdrop) paletteBackdrop.addEventListener("click", (event) => { if (event.target === paletteBackdrop) closeCommandPalette(); });
if (commandSearch) {
  commandSearch.addEventListener("input", () => { commandIndex = 0; renderCommandPalette(); });
  commandSearch.addEventListener("keydown", (event) => {
    const entries = commandList ? [...commandList.querySelectorAll(".command-item")] : [];
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!entries.length) return;
      commandIndex = (commandIndex + (event.key === "ArrowDown" ? 1 : -1) + entries.length) % entries.length;
      renderCommandPalette();
    }
    if (event.key === "Enter" && entries.length) {
      event.preventDefault();
      entries[commandIndex]?.click();
    }
  });
}
function closeMobileNav() {
  document.body.classList.remove("nav-open");
  if (mobileMenu) mobileMenu.setAttribute("aria-expanded", "false");
}
if (mobileMenu) {
  mobileMenu.setAttribute("aria-expanded", "false");
  mobileMenu.addEventListener("click", () => {
    const open = document.body.classList.toggle("nav-open");
    mobileMenu.setAttribute("aria-expanded", String(open));
  });
}
if (sidebarScrim) sidebarScrim.addEventListener("click", closeMobileNav);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") { closeCommandPalette(); closeMobileNav(); }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    focusNavigationSearch();
  }
});
document.querySelectorAll(".quick-link").forEach((a) => {
  a.addEventListener("click", () => switchSection(a.dataset.goto));
});
function switchSection(name) {
  closeMobileNav();
  if (navSearch) { navSearch.value = ""; filterNavigation(); }
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.section === name));
  document.querySelectorAll(".section").forEach((s) => s.classList.toggle("active", s.id === "sec-" + name));
  $("section-title").textContent = TITLES[name] || name;
  if ($("section-context")) $("section-context").textContent = SECTION_CONTEXT[name] || "Рабочее пространство управления ботом.";
  if (name === "settings" && !settingsLoaded) loadSettings();
  if (name === "test") loadTestChannels();
  if (name === "embed") renderPreview();
  if (name === "logs") { loadLogs(); startLogsTimer(); }
  else stopLogsTimer();
  if (name === "audit") { loadAudit(); startAuditTimer(); }
  else stopAuditTimer();
  if (name === "stats") loadStats();
  if (name === "files") loadFiles();
  if (name === "overview") startOverviewTimer();
  else stopOverviewTimer();
  if (name === "server" && !srvLoaded) loadServer();
  if (name === "moderation") loadModeration();
  if (name === "giveaways") loadGiveaways();
  if (name === "tickets") loadTickets();
  if (name === "automod") loadAutomod();
  if (name === "polls") loadPolls();
  if (name === "birthdays") loadBirthdays();
  if (name === "tempvoice") loadTempVoice();
    if (name === "scheduler") loadScheduler();
  if (name === "backup") loadBackup();
}

function resolveColor(str) {
  if (!str) return null;
  const s = String(str).trim().toLowerCase();
  if (COLORS[s] !== undefined) return COLORS[s];
  const m = hex6.exec(s);
  if (m) return parseInt(m[1], 16);
  return null;
}

/* --- обзор --- */
async function loadOverview() {
  const r = await api("/api/overview");
  const d = r.data || {};
  const online = !!d.bot_online;
  const st = $("status");
  $("ov_status").textContent = online ? "Онлайн" : "Офлайн";
  $("ov_status").className = "badge " + (online ? "ok" : "off");
  st.textContent = online ? ("бот онлайн: " + (d.bot_name || "—")) : "бот офлайн";
  st.className = "badge " + (online ? "ok" : "off");
  const live = $("live-indicator");
  const liveText = $("live-indicator-text");
  if (live && liveText) {
    live.className = "live-indicator " + (online ? "online" : "offline");
    liveText.textContent = online ? "Бот на связи" : "Бот офлайн";
  }
  $("guildname").textContent = d.guild && d.guild.name ? "сервер: " + d.guild.name : "";
  $("ov_uptime").textContent = d.uptime || "—";
  $("ov_ping").textContent = (d.latency_ms !== undefined ? String(d.latency_ms) : "—") + " мс";
  $("ov_mem").textContent = d.mem_mb !== undefined ? d.mem_mb + " МБ" : "—";
  $("ov_guild").textContent = d.guild ? d.guild.name : "—";
  $("ov_members").textContent = d.guild ? String(d.guild.members) : "—";
  $("ov_online").textContent = d.guild ? String(d.guild.online) : "—";
  $("ov_channels").textContent = d.guild ? String(d.guild.channels) + " / " + String(d.guild.roles) : "—";
  if (d.bot_name) {
    $("panel-name").textContent = d.bot_name;
    document.title = "Панель — " + d.bot_name;
  }
  loadMonitorSparks();
}
function startOverviewTimer() {
  if (overviewTimer) return;
  overviewTimer = setInterval(() => {
    if (document.hidden) return;
    if (!$("sec-overview").classList.contains("active")) return;
    loadOverview();
  }, 15000);
}
function stopOverviewTimer() {
  if (overviewTimer) { clearInterval(overviewTimer); overviewTimer = null; }
}

/* --- каналы --- */
async function loadChannels() {
  const r = await api("/api/bot/channels");
  const list = (r.data && r.data.channels) || [];
  const sel = $("sel_channel");
  sel.innerHTML = "";
  if (list.length === 0) {
    const o = document.createElement("option");
    o.value = ""; o.textContent = "Нет каналов";
    sel.appendChild(o);
    return;
  }
  list.forEach((ch) => {
    const o = document.createElement("option");
    o.value = ch.id;
    o.textContent = ch.name || ch.id;
    sel.appendChild(o);
  });
}
async function refreshChannels() {
  toast("Загрузка каналов…");
  await loadChannels();
}
/* --- конструктор эмбедов --- */
function setMode(m) {
  mode = m;
  const wb = $("mode-webhook"), bb = $("mode-bot");
  if (wb) wb.classList.toggle("active", m === "webhook");
  if (bb) bb.classList.toggle("active", m === "bot");
  $("block-webhook").style.display = m === "webhook" ? "" : "none";
  $("block-bot").style.display = m === "bot" ? "" : "none";
  if (m === "bot") loadChannels();
}

function renderButtons() {
  const box = $("btn_editor");
  box.innerHTML = "";
  if (btnRows.length === 0) btnRows = [[]];
  btnRows.forEach((row, ri) => {
    const line = tag("div", "btn-row");
    const t = tag("div", "inlinebox", "Ряд " + (ri + 1) + ":");
    line.appendChild(t);
    row.forEach((btn, bi) => {
      const l = tag("input", null);
      l.type = "text"; l.placeholder = "Текст"; l.value = btn.label; l.maxLength = 80;
      l.oninput = () => { btn.label = l.value; renderPreview(); };
      const u = tag("input", null);
      u.type = "url"; u.placeholder = "URL (стиль Ссылка)"; u.value = btn.url || ""; u.maxLength = 255;
      u.oninput = () => { btn.url = u.value.trim(); renderPreview(); };
      const s = tag("select", null);
      ["Primary", "Secondary", "Success", "Danger", "Link"].forEach((n, k) => {
        const o = document.createElement("option");
        o.value = String(k + 1); o.textContent = n;
        s.appendChild(o);
      });
      s.value = String(btn.style || 2);
      s.onchange = () => { btn.style = parseInt(s.value, 10); renderPreview(); };
      const del = tag("button", "btn mini", "✖");
      del.type = "button"; del.title = "Удалить кнопку";
      del.onclick = () => { row.splice(bi, 1); renderButtons(); renderPreview(); };
      line.append(l, u, s, del);
    });
    box.appendChild(line);
  });
}
function addButton() {
  let row = btnRows[btnRows.length - 1];
  if (!row || row.length >= 5) {
    if (btnRows.length >= 5) return toast("Максимум 5 рядов", false);
    row = [];
    btnRows.push(row);
  }
  row.push({ label: "Кнопка", style: 2, url: "" });
  renderButtons(); renderPreview();
}
function addButtonRow() {
  if (btnRows.length >= 5) return toast("Максимум 5 рядов", false);
  btnRows.push([]);
  renderButtons();
}

function newEmbed() {
  return { title: "", desc: "", color: "", author_name: "", author_icon: "", author_url: "", footer_text: "", footer_icon: "", image: "", thumb: "", ts: false, fields: [] };
}
function embedTag(i, key) {
  return "em_" + i + "_" + key;
}
function bindEmbedInput(i, key) {
  return (el) => { el.oninput = () => { embeds[i][key] = el.value; if (key === "color") renderColorFor(i); renderPreview(); }; };
}
function lbl(clazz, text) {
  return tag("label", clazz, text);
}
function renderEmbedFields(i) {
  const box = $(embedTag(i, "fields"));
  if (!box) return;
  const flds = embeds[i].fields;
  box.innerHTML = "";
  if (flds.length === 0) box.appendChild(tag("div", "hint muted", "Поля не добавлены"));
  flds.forEach((f, fi) => {
    const item = tag("div", "field-item");
    const row = tag("div", "row");
    const n = tag("input", null);
    n.type = "text"; n.placeholder = "Название"; n.value = f.name; n.maxLength = 256;
    n.oninput = () => { f.name = n.value; renderPreview(); };
    const v = tag("input", null);
    v.type = "text"; v.placeholder = "Значение"; v.value = f.value; v.maxLength = 1024;
    v.oninput = () => { f.value = v.value; renderPreview(); };
    const inl = tag("select", null);
    ["В ряд", "В столбец"].forEach((t, k) => {
      const o = document.createElement("option");
      o.value = String(k); o.textContent = t;
      inl.appendChild(o);
    });
    inl.value = String(f.inline ? 1 : 0);
    inl.onchange = () => { f.inline = inl.value === "1"; renderPreview(); };
    const del = tag("button", "btn mini", "✖");
    del.type = "button"; del.title = "Удалить поле";
    del.onclick = () => { flds.splice(fi, 1); renderEmbedFields(i); renderPreview(); };
    row.append(n, v, inl, del);
    item.appendChild(row);
    box.appendChild(item);
  });
}
function addField(i) {
  if (embeds[i].fields.length >= 25) return toast("Максимум 25 полей", false);
  embeds[i].fields.push({ name: "Поле", value: "Описание", inline: false });
  renderEmbedFields(i); renderPreview();
}
function addEmbed() {
  if (embeds.length >= 10) return toast("Максимум 10 эмбедов в сообщении", false);
  embeds.push(newEmbed());
  renderEmbeds(); renderPreview();
}
function dupEmbed(i) {
  if (embeds.length >= 10) return toast("Максимум 10 эмбедов в сообщении", false);
  embeds.splice(i + 1, 0, JSON.parse(JSON.stringify(embeds[i])));
  renderEmbeds(); renderPreview();
}
function delEmbed(i) {
  if (embeds.length <= 1) return toast("Должен остаться минимум 1 эмбед", false);
  embeds.splice(i, 1);
  renderEmbeds(); renderPreview();
}
function renderColorFor(i) {
  const c = resolveColor(embeds[i].color);
  const sw = $(embedTag(i, "colsw"));
  if (sw) sw.style.background = c === null ? "transparent" : "#" + c.toString(16).padStart(6, "0");
  const picker = $(embedTag(i, "color_picker"));
  if (picker) picker.value = c === null ? "#5865f2" : "#" + c.toString(16).padStart(6, "0");
}
function pickColorFor(i, hex) {
  const c = resolveColor(hex);
  if (c === null) return;
  embeds[i].color = "#" + c.toString(16).padStart(6, "0");
  const text = $(embedTag(i, "color"));
  if (text) text.value = embeds[i].color;
  renderColorFor(i);
  renderPreview();
}
function renderEmbeds() {
  const box = $("embeds_box");
  box.innerHTML = "";
  embeds.forEach((e, i) => {
    const card = tag("div", "embed-ed");
    card.appendChild(tag("div", "embed-ed-head", "Эмбед " + (i + 1) + " <span class=\"muted small\">(" + (i + 1) + "/10)</span>"));
    const opts = tag("div", "embed-ed-opts");
    const dup = tag("button", "btn mini", "⧉ Дублировать");
    dup.type = "button"; dup.title = "Дублировать эмбед";
    dup.onclick = () => dupEmbed(i);
    const del = tag("button", "btn mini", "✖");
    del.type = "button"; del.title = "Удалить эмбед";
    del.onclick = () => delEmbed(i);
    opts.append(dup, del);
    card.appendChild(opts);

    const inT = tag("input", null);
    inT.type = "text"; inT.id = embedTag(i, "title"); inT.maxLength = 256; inT.placeholder = "Заголовок"; inT.value = e.title;
    const inD = tag("textarea", null);
    inD.id = embedTag(i, "desc"); inD.rows = 4; inD.maxLength = 4000; inD.placeholder = "Описание"; inD.value = e.desc;
    bindEmbedInput(i, "title")(inT);
    bindEmbedInput(i, "desc")(inD);
    card.appendChild(lbl("field", "Заголовок"));
    card.appendChild(inT);
    card.appendChild(lbl("field", "Описание"));
    card.appendChild(inD);

    const inC = tag("input", null);
    inC.type = "text"; inC.id = embedTag(i, "color"); inC.placeholder = "например #5865f2 или blurple"; inC.value = e.color;
    const inP = tag("input", null);
    inP.type = "color"; inP.id = embedTag(i, "color_picker"); inP.value = "#5865f2";
    const sw = tag("span", null);
    sw.id = embedTag(i, "colsw");
    sw.style.cssText = "width:34px;height:34px;border-radius:8px;border:1px solid var(--border);display:inline-block";
    inC.oninput = () => { e.color = inC.value; renderColorFor(i); renderPreview(); };
    inP.oninput = () => pickColorFor(i, inP.value);
    const rowC = tag("div", "row-inline");
    inC.style.cssText = "flex:1;min-width:120px";
    inP.style.cssText = "width:38px;height:38px;padding:2px;border:1px solid var(--border);background:var(--input);border-radius:8px;cursor:pointer";
    rowC.append(inC, inP, sw);
    card.appendChild(lbl("field", "Цвет полоски"));
    card.appendChild(rowC);

    card.appendChild(lbl("field", "Автор — имя"));
    const inAN = tag("input", null);
    inAN.id = embedTag(i, "author_name"); inAN.maxLength = 256; inAN.value = e.author_name;
    bindEmbedInput(i, "author_name")(inAN);
    card.appendChild(inAN);
    card.appendChild(lbl("field", "Автор — иконка (URL)"));
    const inAI = tag("input", null);
    inAI.id = embedTag(i, "author_icon"); inAI.type = "url"; inAI.value = e.author_icon;
    bindEmbedInput(i, "author_icon")(inAI);
    card.appendChild(inAI);
    card.appendChild(lbl("field", "Автор — ссылка"));
    const inAU = tag("input", null);
    inAU.id = embedTag(i, "author_url"); inAU.type = "url"; inAU.value = e.author_url;
    bindEmbedInput(i, "author_url")(inAU);
    card.appendChild(inAU);

    card.appendChild(lbl("field", "Подвал — текст"));
    const inFT = tag("input", null);
    inFT.id = embedTag(i, "footer_text"); inFT.maxLength = 2048; inFT.value = e.footer_text;
    bindEmbedInput(i, "footer_text")(inFT);
    card.appendChild(inFT);
    card.appendChild(lbl("field", "Подвал — иконка (URL)"));
    const inFI = tag("input", null);
    inFI.id = embedTag(i, "footer_icon"); inFI.type = "url"; inFI.value = e.footer_icon;
    bindEmbedInput(i, "footer_icon")(inFI);
    card.appendChild(inFI);

    card.appendChild(lbl("field", "Изображение"));
    const inI = tag("input", null);
    inI.id = embedTag(i, "image"); inI.type = "url"; inI.value = e.image;
    bindEmbedInput(i, "image")(inI);
    const rowI = tag("div", "row-inline");
    const upI = tag("button", "btn small op", "⬆ Загрузить");
    upI.type = "button";
    upI.onclick = () => $(embedTag(i, "file_image")).click();
    const fI = tag("input", null);
    fI.type = "file"; fI.id = embedTag(i, "file_image"); fI.accept = "image/*"; fI.style.display = "none";
    fI.onchange = () => { if (fI.files[0]) uploadFile(fI.files[0], embedTag(i, "image"), (url) => { e.image = url; renderPreview(); }); fI.value = ""; };
    rowI.append(inI, upI, fI);
    card.appendChild(rowI);

    card.appendChild(lbl("field", "Миниатюра"));
    const inT2 = tag("input", null);
    inT2.id = embedTag(i, "thumb"); inT2.type = "url"; inT2.value = e.thumb;
    bindEmbedInput(i, "thumb")(inT2);
    const rowT = tag("div", "row-inline");
    const upT = tag("button", "btn small op", "⬆ Загрузить");
    upT.type = "button";
    upT.onclick = () => $(embedTag(i, "file_thumb")).click();
    const fT = tag("input", null);
    fT.type = "file"; fT.id = embedTag(i, "file_thumb"); fT.accept = "image/*"; fT.style.display = "none";
    fT.onchange = () => { if (fT.files[0]) uploadFile(fT.files[0], embedTag(i, "thumb"), (url) => { e.thumb = url; renderPreview(); }); fT.value = ""; };
    rowT.append(inT2, upT, fT);
    card.appendChild(rowT);

    const tsL = lbl("toggle-holder", "");
    tsL.style.cssText = "margin:8px 0;font-size:13px";
    const swTS = tag("span", "switch", null);
    const cbTS = tag("input", null);
    cbTS.type = "checkbox"; cbTS.id = embedTag(i, "ts"); cbTS.checked = e.ts;
    cbTS.onchange = () => { e.ts = cbTS.checked; renderPreview(); };
    swTS.appendChild(cbTS);
    swTS.appendChild(tag("span", "slider", null));
    tsL.append(swTS, tag("span", null, " Время отправки"));
    card.appendChild(tsL);

    card.appendChild(tag("h5", "sec", "Поля (до 25)"));
    const fldBox = tag("div", null);
    fldBox.id = embedTag(i, "fields");
    card.appendChild(fldBox);
    renderEmbedFields(i);
    const addF = tag("button", "btn op small", "＋ Добавить поле");
    addF.type = "button";
    addF.style.cssText = "margin-top:10px";
    addF.onclick = () => addField(i);
    card.appendChild(addF);

    renderColorFor(i);
    box.appendChild(card);
  });
}

function readEmbed(i) {
  const e = embeds[i];
  const out = {};
  const t = e.title.trim().slice(0, 256);
  const d = e.desc.trim().slice(0, 4000);
  if (t) out.title = t;
  if (d) out.description = d;
  const co = resolveColor(e.color);
  if (co !== null) out.color = co;
  const an = e.author_name.trim().slice(0, 256);
  if (an) out.author = { name: an, icon_url: e.author_icon.trim().slice(0, 2048) || undefined, url: e.author_url.trim().slice(0, 2048) || undefined };
  const ft = e.footer_text.trim().slice(0, 2048);
  if (ft) out.footer = { text: ft, icon_url: e.footer_icon.trim().slice(0, 2048) || undefined };
  const im = e.image.trim().slice(0, 2048);
  if (im) out.image = { url: im };
  const th = e.thumb.trim().slice(0, 2048);
  if (th) out.thumbnail = { url: th };
  if (e.ts) out.timestamp = new Date().toISOString();
  const fs = e.fields.slice(0, 25).filter((f) => f.name.trim() || f.value.trim());
  if (fs.length) out.fields = fs.map((f) => ({ name: f.name.trim().slice(0, 256), value: f.value.trim().slice(0, 1024), inline: !!f.inline }));
  if (Object.keys(out).length === 0) return null;
  return out;
}
function buildPayload() {
  const isBot = mode === "bot";
  const p = {
    content: ($("f_content").value || "").slice(0, 2000) || null,
    embeds: embeds.slice(0, 10).map((_, i) => readEmbed(i)).filter(Boolean),
    components: btnRows.filter((r) => r.length),
  };
  if (isBot) p.channel_id = $("sel_channel").value;
  else p.webhook_url = $("wh_url").value;
  return p;
}
async function sendMsg() {
  const payload = buildPayload();
  const target = mode === "bot" ? payload.channel_id : payload.webhook_url;
  if (!target) return toast(mode === "bot" ? "Укажите канал" : "Укажите Webhook URL", false);
  if (payload.embeds.length === 0 && !payload.content) return toast("Пустое сообщение", false);
  const r = await api("/api/" + (mode === "bot" ? "bot/send" : "webhook/send"), payload);
  if (r.status === 200 && r.data.ok) {
    toast("✅ Отправлено", true);
    if (mode === "bot") loadMsg();
  } else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}
async function editMsg() {
  const mid = $("msg_id").value.trim();
  if (!mid) return toast("Укажите ID сообщения", false);
  const payload = buildPayload();
  const target = mode === "bot" ? payload.channel_id : payload.webhook_url;
  if (!target) return toast(mode === "bot" ? "Укажите канал" : "Укажите Webhook URL", false);
  payload.message_id = mid;
  const r = await api("/api/" + (mode === "bot" ? "bot/edit" : "webhook/edit"), payload);
  if (r.status === 200 && r.data.ok) toast("✅ Сообщение изменено", true);
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}
function clearBuilder() {
  embeds = [newEmbed()];
  btnRows = [[]];
  $("f_content").value = "";
  renderEmbeds(); renderButtons(); renderPreview();
  toast("🧹 Поля очищены", true);
}
async function loadMsg() {
  const mid = $("msg_id").value.trim().replace(/^https:\/\/discord(a)?\.com\/channels\/\d+\/\d+\//, "");
  if (!mid) return toast("Укажите ID сообщения", false);
  const payload = { message_id: mid };
  if (mode === "bot") {
    payload.channel_id = $("sel_channel").value;
    if (!payload.channel_id) return toast("Укажите канал", false);
  } else {
    payload.webhook_url = $("wh_url").value;
    if (!payload.webhook_url) return toast("Укажите Webhook URL", false);
  }
  const r = await api("/api/" + (mode === "bot" ? "bot/fetch" : "webhook/fetch"), payload);
  if (r.status !== 200 || !r.data.ok || !r.data.data) return toast("❌ Сообщение не найдено", false);
  fillEmbed(r.data.data);
  toast("✅ Загружено в конструктор", true);
}
function fillEmbed(msg) {
  const src = (msg.embeds && msg.embeds.length) ? msg.embeds : [null];
  embeds = src.slice(0, 10).map((em) => ({
    title: (em && em.title) ? em.title : "",
    desc: (em && em.description) ? em.description : "",
    color: (em && em.color) ? em.color : "",
    author_name: (em && em.author && em.author.name) ? em.author.name : "",
    author_icon: (em && em.author && em.author.icon_url) ? em.author.icon_url : "",
    author_url: (em && em.author && em.author.url) ? em.author.url : "",
    footer_text: (em && em.footer && em.footer.text) ? em.footer.text : "",
    footer_icon: (em && em.footer && em.footer.icon_url) ? em.footer.icon_url : "",
    image: (em && em.image && em.image.url) ? em.image.url : "",
    thumb: (em && em.thumbnail && em.thumbnail.url) ? em.thumbnail.url : "",
    ts: false,
    fields: (em && em.fields) ? em.fields.map((f) => ({ name: f.name || "", value: f.value || "", inline: !!f.inline })) : [],
  }));
  $("f_content").value = msg.content || "";
  btnRows = msg.components && msg.components.length ? msg.components : [[]];
  renderEmbeds(); renderButtons(); renderPreview();
}

function renderPreview() {
  const box = $("preview");
  const content = ($("f_content").value || "").trim();
  const ems = embeds.slice(0, 10);
  const hasEmbed = ems.some((e) => e.title.trim() || e.desc.trim() || e.color.trim() || e.author_name.trim() || e.footer_text.trim() || e.image.trim() || e.thumb.trim() || e.fields.some((f) => f.name.trim() || f.value.trim()));
  if (!content && !hasEmbed && btnRows.every((r) => r.length === 0)) {
    box.innerHTML = '<div class="empty">Заполните форму, чтобы увидеть предпросмотр</div>';
    return;
  }
  const view = (obj) => escapeHtml(obj);
  const sum = content.length + ems.reduce((acc, e) => acc + e.title.trim().length + e.desc.trim().length + e.fields.reduce((a, f) => a + f.name.trim().length + f.value.trim().length, 0), 0);
  let html = "";
  if (content) html += '<div class="content-preview">' + escapeHtml(content) + "</div>";
  html += '<div class="' + (sum > 6000 ? "warnsum" : "") + '" style="font-size:12px;margin:2px 0 8px;color:var(--muted)">Суммарно: ' + sum + ' / 6000 символов' + (sum > 6000 ? ' — <span style="color:#f23f43">лимит превышен</span>' : "") + "</div>";
  ems.forEach((e) => {
    if (!e.title.trim() && !e.desc.trim() && !e.color.trim() && !e.author_name.trim() && !e.footer_text.trim() && !e.image.trim() && !e.thumb.trim() && !e.fields.some((f) => f.name.trim() || f.value.trim())) return;
    const c = resolveColor(e.color);
    const colorHex = c === null ? "#5865f2" : "#" + c.toString(16).padStart(6, "0");
    const title = e.title.trim();
    const desc = e.desc.trim();
    const an = e.author_name.trim();
    const ft = e.footer_text.trim();
    const im = e.image.trim();
    const th = e.thumb.trim();
    html += '<div class="embed-card" style="border-left-color:' + colorHex + '">';
    if (an) html += '<div class="embed-author">' + escapeHtml(an) + (th ? '<img class="embed-thumb" src="' + escapeHtml(th) + '" alt="">' : "") + "</div>";
    if (title) html += '<div class="embed-title">' + escapeHtml(title) + "</div>";
    if (desc) html += '<div class="embed-desc">' + escapeHtml(desc) + "</div>";
    if (e.fields.length) {
      html += '<div class="fields">';
      e.fields.forEach((f) => {
        html += '<div class="fname">' + view(f.name) + "</div><div class=\"fvalue\">" + view(f.value) + "</div>";
      });
      html += "</div>";
    }
    if (im) html += '<img class="embed-image" src="' + escapeHtml(im) + '" alt="">';
    if (ft) html += '<div class="embed-footer">' + view(ft) + "</div>";
    if (e.ts) html += '<div class="embed-ts">' + new Date().toLocaleString("ru-RU") + "</div>";
    html += "</div>";
  });
  if (btnRows.some((r) => r.length)) {
    html += "<div style=\"margin-top:8px\">";
    btnRows.filter((r) => r.length).forEach((row) => {
      html += "<div style=\"display:flex;gap:6px;margin-bottom:6px;flex-wrap:wrap\">";
      row.forEach((b) => {
        const style = b.style === 5 ? "link" : "s" + (b.style || 2);
        html += b.style === 5
          ? '<a class="btn ' + style + '" style="text-decoration:none" href="' + escapeHtml(b.url || "#") + '" target="_blank" rel="noopener">' + view(b.label || "Кнопка") + "</a>"
          : '<span class="btn ' + style + '">' + view(b.label || "Кнопка") + "</span>";
      });
      html += "</div>";
    });
    html += "</div>";
  }
  box.innerHTML = html;
}
/* --- настройки --- */
async function loadSettings() {
  const r = await api("/api/settings");
  if (r.status !== 200 || !r.data.ok) { toast("❌ Не удалось загрузить настройки", false); return; }
  settingsData = r.data;
  settingsLoaded = true;
  renderSettings();
  renderModules();
  loadModules();
  const am = $("set_automod_enabled");
  am.checked = !!r.data.automod_enabled;
  $("set_blocked_words").value = (r.data.blocked_words || []).join("\n");
}
function channelName(id) {
  if (!id) return "";
  const all = (settingsData && (settingsData.channels || [])) || [];
  for (const c of all) if (c.id === String(id)) return c.name;
  return String(id);
}
function renderSettings() {
  const box = $("settings-groups");
  box.innerHTML = "";
  SETTINGS_GROUPS.forEach((g) => {
    const card = tag("div", "set-group");
    const h = tag("h4", null, g.title);
    card.appendChild(h);
    g.cols.forEach(([col, label]) => {
      const lb = tag("label", "field", label);
      const sel = document.createElement("select");
      const empty = document.createElement("option");
      empty.value = ""; empty.textContent = "* автовыбор (не задано)";
      sel.appendChild(empty);
      (settingsData.channels || []).forEach((ch) => {
        const o = document.createElement("option");
        o.value = ch.id; o.textContent = (ch.category ? ch.category + " / " : "") + ch.name;
        sel.appendChild(o);
      });
      const saved = (settingsData.settings && settingsData.settings[col]) || "";
      if (saved) sel.value = String(saved);
      sel.dataset.col = col;
      sel.onchange = () => { sel.classList.add("touched"); };
      card.append(lb, sel);
    });
    box.appendChild(card);
  });
  renderEffectiveLogs();
}
function renderEffectiveLogs() {
  const effBox = $("effective-logs");
  if (!effBox) return;
  effBox.innerHTML = "";
  const eff = (settingsData && settingsData.effective_logs) || {};
  const names = { log: "Общий лог", member: "Лог участников", message: "Лог сообщений", voice: "Лог голосовых", mod: "Лог модерации", bot: "Лог бота" };
  const keys = Object.keys(names);
  if (keys.every((k) => !eff[k])) {
    effBox.appendChild(tag("div", "muted", "Не задано"));
    return;
  }
  keys.forEach((k) => {
    const v = eff[k];
    const line = tag("div", "kv", null);
    line.append(tag("b", null, escapeHtml(names[k]) + ": "));
    line.append(document.createTextNode(v ? escapeHtml(channelName(v)) + " (#" + v + ")" : "не задан"));
    effBox.appendChild(line);
  });
}
async function saveSettings() {
  const payload = {};
  document.querySelectorAll("#settings-groups select[data-col]").forEach((sel) => {
    payload[sel.dataset.col] = sel.value || "";
  });
  payload.automod_enabled = $("set_automod_enabled").checked;
  payload.blocked_words = $("set_blocked_words").value;
  const r = await api("/api/settings", payload);
  if (r.status === 200 && r.data.ok) toast("✅ Настройки сохранены", true);
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка сохранения", false);
}
function renderModules() {
  const box = $("modules-grid");
  if (!box) return;
  box.innerHTML = "";
  const modules = (modulesData && modulesData.modules) || [];
  if (!modules.length) {
    box.appendChild(tag("div", "muted", "Модули не найдены"));
    return;
  }
  modules.forEach((mod) => box.appendChild(moduleCard(mod)));
}

async function loadModules() {
  const r = await api("/api/modules");
  if (r.status !== 200 || !r.data.ok) { toast("❌ Не удалось загрузить модули", false); return; }
  modulesData = r.data;
  renderModules();
}

function moduleOptions(kind) {
  if (!modulesData) return [];
  if (kind === "channel") return modulesData.channels || [];
  if (kind === "category") return modulesData.categories || [];
  if (kind === "role") return modulesData.roles || [];
  return [];
}

function moduleCard(mod) {
  const card = tag("div", "module-card");
  card.dataset.module = mod.key;
  const head = tag("div", "row-inline", null);
  head.style.justifyContent = "space-between";
  head.append(tag("h5", null, escapeHtml((mod.emoji || "") + " " + mod.title)));
  const actions = tag("div", "row-inline", null);
  actions.style.margin = "0";
  const save = tag("button", "btn success small", "💾");
  save.type = "button";
  save.title = "Сохранить модуль";
  save.onclick = () => saveModule(mod.key, card);
  const reset = tag("button", "btn op small", "↺");
  reset.type = "button";
  reset.title = "Сбросить настройки модуля на .env";
  reset.onclick = () => resetModule(mod.key);
  actions.append(save, reset);
  head.append(actions);
  card.append(head);
  if (mod.description) card.append(tag("div", "muted small", escapeHtml(mod.description)));
  const fields = tag("div", "module-fields", null);
  mod.fields.forEach((f) => fields.append(moduleField(f)));
  card.append(fields);
  return card;
}

function moduleField(f) {
  const wrap = tag("label", "field", null);
  wrap.dataset.key = f.key;
  let caption = escapeHtml(f.label);
  if (f.overridden) caption += ' <span class="pill-env">панель</span>';
  wrap.innerHTML = caption;
  if (f.env) wrap.append(tag("span", "muted small", escapeHtml(f.env)));
  let input;
  if (f.kind === "bool") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = !!f.value;
  } else if (f.kind === "int" || f.kind === "float") {
    input = document.createElement("input");
    input.type = "number";
    if (f.min != null) input.min = f.min;
    if (f.max != null) input.max = f.max;
    input.step = f.kind === "float" ? "0.05" : "1";
    input.value = f.value == null ? "" : f.value;
  } else if (f.kind === "channel" || f.kind === "category" || f.kind === "role") {
    input = document.createElement("select");
    const none = document.createElement("option");
    none.value = "";
    none.textContent = f.kind === "role" ? "* не задано" : "* автовыбор (не задано)";
    input.appendChild(none);
    moduleOptions(f.kind).forEach((o) => {
      const opt = document.createElement("option");
      opt.value = o.id;
      opt.textContent = o.name;
      input.appendChild(opt);
    });
    input.value = f.value == null ? "" : String(f.value);
  } else if (f.kind === "json") {
    input = document.createElement("textarea");
    input.rows = 3;
    input.value = f.value || "";
  } else {
    input = document.createElement("input");
    input.type = "text";
    input.value = f.value == null ? "" : f.value;
  }
  input.dataset.kind = f.kind;
  input.dataset.field = f.key;
  wrap.append(input);
  if (f.hint) wrap.append(tag("span", "muted small", escapeHtml(f.hint)));
  return wrap;
}

function collectModule(card) {
  const values = {};
  card.querySelectorAll("[data-field]").forEach((input) => {
    values[input.dataset.field] = input.dataset.kind === "bool" ? input.checked : input.value;
  });
  return values;
}

async function saveModule(key, card) {
  const r = await api("/api/modules", { module: key, values: collectModule(card) });
  if (r.status === 200 && r.data.ok) {
    toast("✅ Модуль сохранён", true);
    loadModules();
  } else {
    toast(r.data && r.data.error ? "❌ " + r.data.error : "❌ Ошибка сохранения", false);
  }
}

async function resetModule(key) {
  const r = await api("/api/modules/reset", { module: key });
  if (r.status === 200 && r.data.ok) {
    toast("↺ Модуль сброшен на .env", true);
    loadModules();
  } else {
    toast("❌ Не удалось сбросить модуль", false);
  }
}

/* --- тест --- */
async function loadTestChannels() {
  const r = await api("/api/bot/channels");
  const list = (r.data && r.data.channels) || [];
  const sel = $("test_channel");
  sel.innerHTML = "";
  if (list.length === 0) {
    const o = document.createElement("option");
    o.value = ""; o.textContent = "Нет каналов";
    sel.appendChild(o);
    return;
  }
  list.forEach((ch) => {
    const o = document.createElement("option");
    o.value = ch.id; o.textContent = ch.name || ch.id;
    sel.appendChild(o);
  });
}
async function sendTest() {
  const channel_id = $("test_channel").value;
  const content = $("test_content").value.trim();
  if (!channel_id) return toast("Выберите канал", false);
  if (!content) return toast("Введите текст", false);
  const r = await api("/api/bot/send", { channel_id, content: content.slice(0, 2000), embeds: [] });
  $("test_result").textContent = r.status === 200 && r.data.ok ? "✅ Отправлено" : ("❌ " + (r.data.error || "Ошибка"));
}

/* --- мониторинг (мини-графики) --- */
function drawSpark(id, series, color) {
  const c = $(id);
  if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  const w = (c.parentElement.clientWidth || 280) * dpr;
  const h = 56 * dpr;
  if (c.width !== w) c.width = w;
  if (c.height !== h) c.height = h;
  const ctx = c.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  const vals = (series || []).map((p) => Number(p && p.v)).filter((v) => Number.isFinite(v));
  ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 2 * dpr; ctx.lineJoin = "round"; ctx.lineCap = "round";
  if (vals.length === 0) {
    ctx.font = (11 * dpr) + "px Segoe UI";
    ctx.fillStyle = "rgba(152,160,179,.7)";
    ctx.textAlign = "center";
    ctx.fillText("нет данных", w / 2, h / 2);
    return;
  }
  let min = Math.min(...vals), max = Math.max(...vals);
  if (max === min) { max = min + 1; min = min - 1; }
  const pad = 6 * dpr;
  const px = (i) => pad + (vals.length <= 1 ? 0 : i * (w - pad * 2) / (vals.length - 1));
  const py = (v) => h - pad - (v - min) / (max - min) * (h - pad * 2);
  ctx.beginPath();
  vals.forEach((v, i) => (i === 0 ? ctx.moveTo(px(i), py(v)) : ctx.lineTo(px(i), py(v))));
  ctx.stroke();
  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, color + "44"); grad.addColorStop(1, color + "00");
  ctx.lineTo(px(vals.length - 1), h); ctx.lineTo(px(0), h); ctx.closePath();
  ctx.fillStyle = grad; ctx.fill();
  const cur = vals[vals.length - 1];
  ctx.beginPath(); ctx.arc(px(vals.length - 1), py(cur), 3 * dpr, 0, Math.PI * 2); ctx.fill();
}
async function loadMonitorSparks() {
  const r = await api("/api/monitor");
  const d = r.data || {};
  drawSpark("spark_lat", d.latency || [], "#23a55a");
  drawSpark("spark_mem", d.mem || [], "#f2780d");
  drawSpark("spark_online", d.online || [], "#5865f2");
  const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
  const lastOf = (arr) => Array.isArray(arr) && arr.length ? arr[arr.length - 1].v : null;
  set("spark_lat_num", d.latency_ms != null ? d.latency_ms + " мс" : (lastOf(d.latency) != null ? lastOf(d.latency) + " мс" : "—"));
  set("spark_mem_num", d.mem_mb != null ? d.mem_mb + " МБ" : (lastOf(d.mem) != null ? lastOf(d.mem) + " МБ" : "—"));
  const onl = d.guild && d.guild.online != null ? d.guild.online : lastOf(d.online);
  set("spark_online_num", onl != null ? String(onl) : "—");
  const last = $("mon_last");
  if (last) last.textContent = r.status === 200 ? "· обновлено " + new Date().toLocaleTimeString("ru-RU") : "";
}

/* --- сервер --- */
let srvLoaded = false;
let srvCat = [];
let srvMembersFull = [];
let srvRoles = [];
function fmtCount(n) { return n >= 1000 ? (n / 1000).toFixed(1).replace(".", ",") + "k" : String(n); }
function relTime(iso) {
  const d = new Date(iso); if (isNaN(d)) return iso;
  const s = Math.round((d - Date.now()) / 1000), sign = s < 0 ? -1 : 1, a = Math.abs(s);
  const txt = a < 60 ? a + " с" : a < 3600 ? Math.floor(a / 60) + " мин" : a < 86400 ? Math.floor(a / 3600) + " ч" : Math.floor(a / 86400) + " д";
  return (sign < 0 ? "через " : a > 0 ? "" : "сейчас") + txt;
}
async function loadServer() {
  const r = await api("/api/server");
  if (r.status !== 200 || !r.data.ok) { $("srv_head").innerHTML = '<div class="muted">Не удалось загрузить сервер</div>'; return; }
  srvLoaded = true;
  const d = r.data.guild || {};
  srvCat = r.data.categories || [];
  srvRoles = r.data.roles || [];
  $("srv_head").innerHTML =
    '<div class="guild-hero">' +
    (d.icon ? '<img src="' + escapeHtml(d.icon) + '" alt="">' : '<div class="brand-ic" style="width:64px;height:64px;line-height:64px;font-size:28px">' + escapeHtml((d.name || "?").charAt(0).toUpperCase()) + "</div>") +
    "<div>" +
    '<h4>' + escapeHtml(d.name || "—") + (d.level ? ' <span class="chip">' + escapeHtml(d.level) + "</span>" : "") + "</h4>" +
    (d.description ? '<p>' + escapeHtml(d.description) + "</p>" : "") +
    '<div class="row-actions" style="margin-top:6px">' +
    '<span class="chip">Участников <b>' + fmtCount(d.members || 0) + "</b></span>" +
    '<span class="chip">Онлайн <b>' + fmtCount(d.online || 0) + "</b></span>" +
    '<span class="chip">Бустов <b>' + fmtCount(d.boosts || 0) + "</b></span>" +
    '<span class="chip">Каналов <b>' + (d.channels || 0) + "</b></span>" +
    '<span class="chip">Ролей <b>' + (d.roles || 0) + "</b></span>" +
    (d.owner ? '<span class="chip">Владелец <b>' + escapeHtml(d.owner) + "</b></span>" : "") +
    "</div>" +
    '<div class="row-actions" style="margin-top:6px">' +
    (d.created_at ? '<span class="chip">Создан ' + new Date(d.created_at).toLocaleDateString("ru-RU") + "</span>" : "") +
    (d.me_permissions || []).map((p) => '<span class="chip">Бот: ' + escapeHtml(p) + "</span>").join("") +
    "</div>" +
    "</div></div>";
  renderChannelsTree();
  renderRoles();
  fillRoleSelect();
  loadServerMembers();
}
function renderChannelsTree() {
  const box = $("srv_channels");
  box.innerHTML = "";
  if (!srvCat.length) { box.appendChild(tag("div", "muted", "Каналы недоступны")); return; }
  srvCat.forEach((cat) => {
    box.appendChild(tag("div", "cat-name", escapeHtml(cat.name || "Без категории")));
    cat.channels.forEach((ch) => {
      const isV = ch.type === "voice";
      const row = tag("div", "channel-row");
      row.title = "Клик — копировать ID";
      row.onclick = () => copyText(ch.id);
      row.innerHTML =
        '<span class="ic">' + (isV ? "🔊" : "#") + "</span>" +
        '<span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + escapeHtml(ch.name) + "</span>" +
        (ch.nsfw ? '<span class="chip">NSFW</span>' : "") +
        (ch.slowmode ? '<span class="chip small">' + ch.slowmode + " с</span>" : "") +
        (isV ? '<span class="chip small">' + (ch.voice_online || 0) + " 🎤</span>" : "");
      box.appendChild(row);
      if (isV && ch.voice_users && ch.voice_users.length) {
        box.appendChild(tag("div", "vc-users", "🎤 " + ch.voice_users.map((u) => escapeHtml(u.display_name)).join(", ")));
      }
    });
  });
}
function renderRoles() {
  const box = $("srv_roles");
  box.innerHTML = "";
  if (!srvRoles.length) { box.appendChild(tag("div", "muted", "Роли недоступны")); return; }
  srvRoles.forEach((role) => {
    const line = tag("div", "listline");
    line.title = "Клик — копировать ID";
    line.onclick = () => copyText(role.id);
    line.innerHTML =
      '<span class="pill-role" style="background:' + escapeHtml(role.color) + "22;color:" + escapeHtml(role.color) + '">●</span>' +
      '<span class="grow">' + escapeHtml(role.name) + (role.managed ? ' <span class="chip">интегрированная</span>' : "") + "</span>" +
      (role.hoist ? '<span class="chip">в списке</span>' : "") +
      '<span class="chip">' + fmtCount(role.member_count) + "</span>";
    box.appendChild(line);
  });
}
function fillRoleSelect() {
  const sel = $("role_select");
  sel.innerHTML = "";
  srvRoles.forEach((role) => {
    const o = document.createElement("option");
    o.value = role.id; o.textContent = role.name;
    sel.appendChild(o);
  });
}
async function loadServerMembers() {
  const r = await api("/api/server/members?q=");
  const list = (r.data && r.data.members) || [];
  srvMembersFull = list;
  ["member_list", "member_list_mod"].forEach((id) => {
    const dl = $(id);
    if (!dl) return;
    dl.innerHTML = "";
    list.forEach((m) => {
      const o = document.createElement("option");
      o.value = m.is_bot ? m.name + " (бот) #" + m.id : m.display_name + " #" + m.id;
      dl.appendChild(o);
    });
  });
}
async function roleAction(act) {
  const member = $("role_member").value.trim();
  const role = $("role_select").value;
  $("role_result").textContent = "";
  if (!member) return toast("Укажите участника", false);
  if (!role) return toast("Укажите роль", false);
  const r = await api("/api/server/members/roles", { member, role_id: role, action: act });
  if (r.status === 200 && r.data.ok) {
    $("role_result").textContent = (act === "add" ? "✅ Роль выдана" : "✅ Роль снята") + ": " + r.data.member_name + " → " + r.data.role_name;
    loadServerMembers();
    toast(r.data.applied ? "✅ Готово" : "ℹ️ Уже в таком состоянии", r.data.applied);
  } else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}

/* --- модерация --- */
let allWarns = [];
let modMember = null;
async function loadModeration() {
  loadServerMembers();
  loadWarns();
  const info = $("mod_member");
  if (info) info.textContent = "Введите участника и нажмите «Найти».";
  $("mod_actions").style.display = "none";
}
async function loadWarns() {
  const r = await api("/api/moderation/warns");
  allWarns = (r.data && r.data.warns) || [];
  renderWarns();
}
function renderWarns() {
  const box = $("warns_box");
  const q = ($("warn_search").value || "").toLowerCase().trim();
  box.innerHTML = "";
  const list = q ? allWarns.filter((w) => (w.user_name || "").toLowerCase().includes(q) || (w.user_id || "").includes(q)) : allWarns;
  $("warns_total").textContent = list.length ? "· всего: " + list.length : "";
  if (!list.length) { box.appendChild(tag("div", "muted", "Предупреждений нет")); return; }
  list.forEach((w) => {
    const line = tag("div", "listline");
    line.innerHTML =
      '<span class="chip" style="background:rgba(242,63,67,.14);color:#f23f43">#' + w.id + "</span>" +
      '<span class="grow"><b>' + escapeHtml(w.user_name || w.user_id) + '</b> <span class="sub">· модератор: ' + escapeHtml(w.moderator_name || w.moderator_id) + "</span></span>" +
      '<span class="sub">' + new Date(w.created_at).toLocaleString("ru-RU") + "</span>" +
      '<span class="sub" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1">' + escapeHtml(w.reason) + "</span>";
    const del = tag("button", "btn mini danger", "✖");
    del.type = "button"; del.title = "Снять предупреждение";
    del.onclick = async () => {
      const rr = await api("/api/moderation/warns/" + w.id, null, "DELETE");
      if (rr.status === 200 && rr.data.ok) { toast("✅ Предупреждение #" + w.id + " снято", true); loadWarns(); if (modMember) refreshModMember(); }
      else toast("❌ Не удалось снять", false);
    };
    line.appendChild(del);
    box.appendChild(line);
  });
}
async function findMember(query) {
  if (!query) return null;
  const r = await api("/api/server/members?q=" + encodeURIComponent(query));
  const list = (r.data && r.data.members) || [];
  if (list.length === 1) return list[0];
  const q = query.toLowerCase();
  const exact = list.find((m) => m.display_name.toLowerCase() === q || m.name.toLowerCase() === q || m.id === query.replace(/[#\s]/g, ""));
  return exact || (list.length ? list[0] : null);
}
async function loadModMember() {
  const v = $("mod_target").value.trim();
  if (!v) return toast("Введите участника", false);
  const m = await findMember(v);
  if (!m) { $("mod_member").textContent = "Участник не найден."; $("mod_actions").style.display = "none"; return; }
  modMember = m;
  $("mod_member").innerHTML = "";
  const p = $("mod_member");
  const line = tag("div", "listline");
  line.innerHTML =
    '<img class="av" src="' + escapeHtml(m.avatar) + '" alt="">' +
    '<span class="grow"><b>' + escapeHtml(m.display_name) + '</b> <span class="sub">' + escapeHtml(m.name) + " # " + m.id + "</span></span>" +
    '<span class="chip" style="color:' + escapeHtml(m.top_role_color) + '">' + escapeHtml(m.top_role) + "</span>" +
    (m.is_bot ? '<span class="chip">бот</span>' : "") +
    '<span class="chip">варнов: ' + (m.warnings || 0) + "</span>";
  p.appendChild(line);
  $("mod_actions").style.display = "";
}
async function refreshModMember() {
  const v = $("mod_target").value.trim() || (modMember && modMember.id);
  if (!v) return;
  const m = await findMember(v);
  if (m) { modMember = m; const el = $("mod_member"); el.innerHTML = ""; const line = tag("div", "listline"); line.innerHTML =
    '<img class="av" src="' + escapeHtml(m.avatar) + '" alt="">' +
    '<span class="grow"><b>' + escapeHtml(m.display_name) + '</b> <span class="sub">#' + m.id + "</span></span>" +
    '<span class="chip">варнов: ' + (m.warnings || 0) + "</span>"; el.appendChild(line); }
}
function modReason() { return $("mod_reason").value.trim(); }
async function modWarn() {
  if (!modMember) return toast("Сначала найдите участника", false);
  const r = await api("/api/moderation/warn", { member: modMember.id, reason: modReason() || "Без причины" });
  if (r.status === 200 && r.data.ok) { toast("⚠ Варн выдан, всего: " + r.data.count, true); $("mod_reason").value = ""; loadWarns(); refreshModMember(); }
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}
async function modKick() {
  if (!modMember) return toast("Сначала найдите участника", false);
  if (!confirm("Кикнуть " + modMember.display_name + "?")) return;
  const r = await api("/api/moderation/kick", { member: modMember.id, reason: modReason() });
  if (r.status === 200 && r.data.ok) { toast("👢 Участник кикнут", true); $("mod_actions").style.display = "none"; modMember = null; $("mod_target").value = ""; }
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}
async function modBan() {
  if (!modMember) return toast("Сначала найдите участника", false);
  if (!confirm("Забанить " + modMember.display_name + "?")) return;
  const r = await api("/api/moderation/ban", { member: modMember.id, reason: modReason(), delete_days: 0 });
  if (r.status === 200 && r.data.ok) { toast("🔨 Участник забанен", true); $("mod_actions").style.display = "none"; modMember = null; $("mod_target").value = ""; }
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}
async function modTimeout() {
  if (!modMember) return toast("Сначала найдите участника", false);
  const minutes = parseInt($("mod_timeout_min").value, 10) || 10;
  if (!confirm("Тайм-аут " + modMember.display_name + " на " + minutes + " мин?")) return;
  const r = await api("/api/moderation/timeout", { member: modMember.id, duration_seconds: minutes * 60, reason: modReason() });
  if (r.status === 200 && r.data.ok) toast("⏳ Тайм-аут до " + new Date(r.data.until).toLocaleTimeString("ru-RU"), true);
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}
async function modClear() {
  if (!modMember) return toast("Сначала найдите участника", false);
  if (!confirm("Снять все предупреждения у " + modMember.display_name + "?")) return;
  const r = await api("/api/moderation/clear", { member: modMember.id });
  if (r.status === 200 && r.data.ok) { toast("🧹 Снято варнов: " + r.data.cleared, true); loadWarns(); refreshModMember(); }
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}
async function modUnban() {
  const promptId = prompt("ID пользователя для разбана:");
  if (!promptId) return;
  const r = await api("/api/moderation/unban", { user_id: promptId.trim(), reason: "Разбан из панели" });
  if (r.status === 200 && r.data.ok) toast("♻️ Разбанен: " + r.data.user_name, true);
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}

/* --- розыгрыши --- */
let gvData = null;
async function loadGiveaways() {
  const chSel = $("gv_channel");
  if (chSel && !chSel.options.length) {
    const ch = await api("/api/bot/channels");
    const list = (ch.data && ch.data.channels) || [];
    chSel.innerHTML = "";
    list.forEach((c) => {
      const o = document.createElement("option");
      o.value = c.id; o.textContent = (c.category ? c.category + " / " : "") + c.name;
      chSel.appendChild(o);
    });
  }
  const r = await api("/api/giveaways");
  gvData = r.data || {};
  renderGiveaways();
}
function gvLine(g, finished) {
  const line = tag("div", "listline");
  line.innerHTML =
    '<span class="chip" style="background:rgba(35,165,90,.14);color:#23a55a">#' + g.id + "</span>" +
    '<span class="grow"><b>' + escapeHtml(g.prize) + '</b> <span class="sub">· ' + escapeHtml(g.channel_name) + " · автор: " + escapeHtml(g.author_name) + "</span></span>" +
    '<span class="chip">👥 ' + g.entries + (g.min_days ? " · 🕛 " + g.min_days + " дн" : "") + "</span>" +
    '<span class="chip">🏆 ' + g.winners + "</span>" +
    '<span class="chip">' + (finished ? "завершён" : "до " + relTime(g.ends_at)) + "</span>";
  const acts = tag("div", "row-actions");
  const jump = tag("button", "btn mini", "🔗");
  jump.type = "button"; jump.title = "Скопировать ID сообщения: " + (g.message_id || "—");
  jump.onclick = () => copyText(g.message_id || "—");
  acts.appendChild(jump);
  if (!finished) {
    const end = tag("button", "btn mini", "⏹ Завершить");
    end.type = "button";
    end.onclick = async () => {
      if (!confirm("Завершить розыгрыш «" + g.prize + "»?")) return;
      const rr = await api("/api/giveaways/end", { message_id: g.message_id });
      if (rr.status === 200 && rr.data.ok) toast("🎉 Завершён. Победители: " + rr.data.winners, true);
      else toast(rr.data.error ? "❌ " + rr.data.error : "❌ Ошибка", false);
      loadGiveaways();
    };
    acts.appendChild(end);
  } else {
    const rer = tag("button", "btn mini", "🔁 Переразыграть");
    rer.type = "button";
    rer.onclick = async () => {
      if (!confirm("Переразыграть приз «" + g.prize + "»?")) return;
      const rr = await api("/api/giveaways/reroll", { message_id: g.message_id });
      if (rr.status === 200 && rr.data.ok) toast("🔁 Новые победители: " + rr.data.winners, true);
      else toast(rr.data.error ? "❌ " + rr.data.error : "❌ Ошибка", false);
      loadGiveaways();
    };
    acts.appendChild(rer);
  }
  line.appendChild(acts);
  return line;
}
function renderGiveaways() {
  const aBox = $("gv_active"), fBox = $("gv_finished");
  aBox.innerHTML = "";
  fBox.innerHTML = "";
  const act = (gvData.active || []), fin = (gvData.finished || []);
  if (!act.length) aBox.appendChild(tag("div", "muted", "Активных розыгрышей нет"));
  act.forEach((g) => aBox.appendChild(gvLine(g, false)));
  if (!fin.length) fBox.appendChild(tag("div", "muted", "Завершённых пока нет"));
  fin.forEach((g) => fBox.appendChild(gvLine(g, true)));
}
async function createGiveaway() {
  const channel_id = $("gv_channel").value;
  const prize = $("gv_prize").value.trim();
  const winners = parseInt($("gv_winners").value, 10) || 1;
  const minutes = parseInt($("gv_minutes").value, 10) || 60;
  const min_days = parseInt($("gv_mindays").value, 10) || 0;
  if (!channel_id) return toast("Выберите канал", false);
  if (!prize) return toast("Укажите приз", false);
  const r = await api("/api/giveaways/create", { channel_id, prize, winners, duration_minutes: minutes, min_days });
  if (r.status === 200 && r.data.ok) {
    toast("🎯 Розыгрыш запущен (#" + r.data.id + ")", true);
    $("gv_prize").value = "";
    loadGiveaways();
  } else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}

/* --- тикеты --- */
let tkData = null;
async function loadTickets() {
  const [r, p] = await Promise.all([api("/api/tickets"), api("/api/tickets/panel")]);
  tkData = r.data || { open: [], closed: [] };
  renderTickets();
  const catSel = $("tk_category"), chSel = $("tk_channel");
  if (catSel && p.data && p.data.ok && !catSel.dataset.ready) {
    catSel.innerHTML = '<option value="">— не выбрана —</option>' +
      (p.data.categories || []).map((c) => '<option value="' + escapeHtml(c.id) + '">' + escapeHtml(c.name) + "</option>").join("");
    if (p.data.category_id) catSel.value = p.data.category_id;
    catSel.dataset.ready = "1";
  }
  if (chSel && p.data && p.data.ok && !chSel.options.length) {
    chSel.innerHTML = "";
    const list = p.data.channels || [];
    if (!list.length) chSel.appendChild(tag("option", "", "— нет доступных каналов —"));
    else list.forEach((c) => {
      const o = document.createElement("option");
      o.value = c.id; o.textContent = (c.category ? c.category + " / " : "") + c.name;
      chSel.appendChild(o);
    });
  }
  const texts = (p.data && p.data.texts) || {};
  for (const field of ["tk_title","tk_desc","tk_footer","tk_open_label","tk_open_emoji","tk_intro_title","tk_intro_desc","tk_intro_footer","tk_close_label","tk_close_emoji","tk_prefix"]) {
    const el = document.getElementById(field);
    if (el) el.value = texts[field] !== undefined ? texts[field] : "";
  }
}
async function saveTicketTexts() {
  const payload = {};
  const map = { tk_title:"ticket_panel_title", tk_desc:"ticket_panel_description", tk_footer:"ticket_panel_footer", tk_open_label:"ticket_open_label", tk_open_emoji:"ticket_open_emoji", tk_intro_title:"ticket_intro_title", tk_intro_desc:"ticket_intro_description", tk_intro_footer:"ticket_intro_footer", tk_close_label:"ticket_close_label", tk_close_emoji:"ticket_close_emoji", tk_prefix:"ticket_channel_prefix" };
  for (const id in map) {
    const el = document.getElementById(id);
    if (el) payload[map[id]] = el.value;
  }
  const r = await api("/api/tickets/panel", payload);
  if (r.status === 200 && r.data.ok) toast("💾 Тексты тикетов сохранены", true);
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}
async function saveTicketCategory() {
  const catSel = $("tk_category");
  if (!catSel) return;
  if (!catSel.value) return toast("Выберите категорию", false);
  const r = await api("/api/tickets/panel", { category_id: catSel.value });
  if (r.status === 200 && r.data.ok) toast("💾 Категория тикетов сохранена", true);
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}
async function sendTicketPanel() {
  const chSel = $("tk_channel");
  if (!chSel) return;
  if (!chSel.value) return toast("Выберите канал", false);
  const category_id = $("tk_category") && $("tk_category").value ? $("tk_category").value : "";
  const r = await api("/api/tickets/panel", { channel_id: chSel.value, category_id });
  if (r.status === 200 && r.data.ok) toast("🎫 Панель отправлена в " + chSel.selectedOptions[0].textContent, true);
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}
function relTicketTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return escapeHtml(String(iso));
  return d.toLocaleString("ru-RU");
}
function tkLine(t, open) {
  const line = tag("div", "listline");
  const chan = t.channel_mention || "#" + t.channel_id;
  line.innerHTML =
    '<span class="chip" style="background:rgba(88,101,242,.16);color:#8ea1ff">#' + t.id + "</span>" +
    '<span class="grow"><b>' + escapeHtml(t.creator_name || t.creator_id) + "</b> " +
    '<span class="sub">· ' + (t.channel_name ? escapeHtml(t.channel_name) : "(канал удалён)") + ' · открыт ' + relTicketTime(t.created_at) +
    (open ? "" : " · закрыт " + relTicketTime(t.closed_at)) +
    (t.has_transcript ? " · 📄 транскрипт" : "") + "</span></span>" +
    '<span class="chip">' + (open ? "открыт" : "закрыт") + "</span>";
  const acts = tag("div", "row-actions");
  if (t.has_transcript) {
    const dl = tag("button", "btn mini", "📄");
    dl.type = "button"; dl.title = "Скачать транскрипт";
    dl.onclick = () => downloadBlob("/api/tickets/" + t.id + "/transcript");
    acts.appendChild(dl);
  }
  if (open) {
    const close = tag("button", "btn mini danger", "🔒 Закрыть");
    close.type = "button";
    close.onclick = async () => {
      if (!confirm("Закрыть тикет #" + t.id + " (" + (t.creator_name || t.creator_id) + ")? Канал будет удалён.")) return;
      const rr = await api("/api/tickets/" + t.id + "/close", {});
      if (rr.status === 200 && rr.data.ok) toast("🔒 Тикет #" + t.id + " закрыт", true);
      else toast(rr.data.error ? "❌ " + rr.data.error : "❌ Ошибка", false);
      loadTickets();
    };
    acts.appendChild(close);
  }
  line.appendChild(acts);
  return line;
}
function renderTickets() {
  const openBox = $("tk_open"), closedBox = $("tk_closed");
  if (!openBox || !closedBox) return;
  openBox.innerHTML = "";
  closedBox.innerHTML = "";
  const open = tkData.open || [], closed = tkData.closed || [];
  if (!open.length) openBox.appendChild(tag("div", "muted", "Открытых тикетов нет"));
  open.forEach((t) => openBox.appendChild(tkLine(t, true)));
  if (!closed.length) closedBox.appendChild(tag("div", "muted", "Закрытых тикетов нет"));
  closed.forEach((t) => closedBox.appendChild(tkLine(t, false)));
}

/* --- автомод --- */
let amData = null;
async function loadAutomod() {
  const r = await api("/api/automod");
  if (r.status !== 200) {
    if (r.data.error) toast("❌ " + r.data.error, false);
    return;
  }
  amData = r.data;
  const en = $("am_enabled"), words = $("am_words"), env = $("am_env"), eff = $("am_effective");
  if (!en || !words || !env || !eff) return;
  en.checked = !!amData.db_enabled;
  words.value = (amData.blocked_words || []).join("\n");
  const cfg = amData.env || {};
  const ign = (cfg.ignored_channels || []).map((c) => escapeHtml(c.name || c.id)).join(", ") || "—";
  eff.textContent = amData.enabled ? "— активен" : "— выключен";
  env.innerHTML =
    '<div style="margin-bottom:6px">Стоп-слова (.env): <b>' + escapeHtml(cfg.banned_words || "—") + "</b></div>" +
    '<div style="margin-bottom:6px">Блок ссылок: <b>' + (cfg.block_links ? "вкл" : "выкл") + "</b></div>" +
    '<div style="margin-bottom:6px">Капс: порог <b>' + cfg.caps_threshold + "</b>, мин. длина <b>" + cfg.caps_min_len + "</b></div>" +
    '<div style="margin-bottom:6px">Сообщений в окне: <b>' + cfg.max_messages + "</b> · таймаут: <b>" + cfg.timeout_seconds + "с</b></div>" +
    '<div style="margin-bottom:6px">Бан после <b>' + cfg.ban_after + "</b> нарушений за <b>" + cfg.ban_window + "с</b></div>" +
    '<div style="margin-bottom:6px">Игнор-роли: <b>' + escapeHtml((cfg.ignore_roles || []).join(", ") || "—") + "</b></div>" +
    '<div style="margin-bottom:6px">Игнор-каналы: <b>' + ign + "</b></div>" +
    '<div style="margin-bottom:6px">Anti-raid: <b>' + (cfg.anti_raid && cfg.anti_raid.enabled ? "вкл" : "выкл") +
    "</b> · порог входов: <b>" + (cfg.anti_raid ? cfg.anti_raid.join_threshold : "—") + "</b></div>" +
    '<div>Regex-исключения: <b>' + escapeHtml(cfg.exempt_regex || "—") + "</b></div>";
}
async function saveAutomod() {
  const en = $("am_enabled"), words = $("am_words");
  if (!en || !words) return;
  const r = await api("/api/automod", {
    enabled: en.checked,
    words: words.value.split("\n").map((w) => w.trim()).filter(Boolean),
  });
  if (r.status === 200 && r.data.ok) { toast("💾 Автомод сохранён", true); loadAutomod(); }
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}
async function activateLockdown() {
  if (!confirm("Закрыть отправку сообщений для @everyone на время lockdown?")) return;
  const r = await api("/api/automod/lockdown", { seconds: 300 });
  if (r.status === 200 && r.data.ok) toast("🔒 Lockdown включён для " + r.data.channels + " каналов", true);
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Не удалось включить lockdown", false);
}

/* --- опросы --- */
let plData = null;
async function loadPolls() {
  const chSel = $("pl_channel");
  const r = await api("/api/polls");
  if (r.status !== 200) {
    if (r.data.error) toast("❌ " + r.data.error, false);
    return;
  }
  plData = r.data;
  if (chSel && !chSel.options.length) {
    chSel.innerHTML = "";
    (r.data.channels || []).forEach((c) => {
      const o = document.createElement("option");
      o.value = c.id; o.textContent = (c.category ? c.category + " / " : "") + c.name;
      chSel.appendChild(o);
    });
  }
  renderPolls();
}
function pollLine(p, active) {
  const line = tag("div", "listline");
  const counts = p.counts || {};
  const bars = (p.options || []).map((opt, i) => {
    const votes = counts[i] || 0;
    const pct = p.total ? Math.round(votes / p.total * 100) : 0;
    return '<div class="row-inline" style="gap:6px"><span class="grow">' + escapeHtml(opt) + '</span><span class="chip">' + votes + " (" + pct + "%)</span></div>";
  }).join("");
  line.innerHTML =
    '<span class="chip" style="background:rgba(88,101,242,.16);color:#8ea1ff">#' + p.id + "</span>" +
    '<span class="grow"><b>' + escapeHtml(p.question) + "</b> " +
    '<span class="sub">· ' + escapeHtml(p.author_name) + " · " + escapeHtml(p.channel_name) + " · голосов: " + p.total + "</span>" +
    '<div style="margin-top:4px">' + bars + "</div></span>";
  const acts = tag("div", "row-actions");
  if (active) {
    const end = tag("button", "btn mini danger", "⬛ Завершить");
    end.type = "button";
    end.onclick = async () => {
      if (!confirm("Завершить опрос #" + p.id + "? Итоги уйдут в канал.")) return;
      const rr = await api("/api/polls/" + p.id + "/end", {});
      if (rr.status === 200 && rr.data.ok) toast("📊 Опрос #" + p.id + " завершён", true);
      else toast(rr.data.error ? "❌ " + rr.data.error : "❌ Ошибка", false);
      loadPolls();
    };
    acts.appendChild(end);
  }
  line.appendChild(acts);
  return line;
}
function renderPolls() {
  const act = $("pl_active"), fin = $("pl_finished");
  if (!act || !fin || !plData) return;
  act.innerHTML = "";
  fin.innerHTML = "";
  const polls = plData.polls || [];
  const active = polls.filter((p) => p.active), finished = polls.filter((p) => !p.active);
  if (!active.length) act.appendChild(tag("div", "muted", "Активных опросов нет"));
  active.forEach((p) => act.appendChild(pollLine(p, true)));
  if (!finished.length) fin.appendChild(tag("div", "muted", "Завершённых опросов нет"));
  finished.forEach((p) => fin.appendChild(pollLine(p, false)));
}
async function createPoll() {
  const chSel = $("pl_channel"), q = $("pl_question"), opts = $("pl_options");
  if (!chSel || !q || !opts) return;
  const options = opts.value.split("\n").map((o) => o.trim()).filter(Boolean);
  if (!options.length) return toast("Введите варианты ответа", false);
  const r = await api("/api/polls/create", { channel_id: chSel.value, question: q.value, options });
  if (r.status === 200 && r.data.ok) { toast("🗳 Опрос #" + r.data.poll_id + " создан", true); q.value = ""; opts.value = ""; loadPolls(); }
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}

/* --- дни рождения --- */
let bdData = null;
async function loadBirthdays() {
  const r = await api("/api/birthdays");
  if (r.status !== 200) {
    if (r.data.error) toast("❌ " + r.data.error, false);
    return;
  }
  bdData = r.data;
  const sel = $("bd_member"), ch = $("bd_channel");
  if (sel && !sel.options.length) {
    sel.innerHTML = "";
    sel.appendChild(tag("option", "", "— выберите участника —"));
    (r.data.members || []).forEach((m) => {
      const o = document.createElement("option");
      o.value = m.id; o.textContent = m.name;
      sel.appendChild(o);
    });
  }
  if (ch) ch.textContent = r.data.channel_id ? "Канал <b>#" + r.data.channel_id + "</b>" : "не задан — анонс не отправляется";
  renderBirthdays();
}
function renderBirthdays() {
  const box = $("bd_list");
  if (!box || !bdData) return;
  box.innerHTML = "";
  const list = bdData.birthdays || [];
  if (!list.length) { box.appendChild(tag("div", "muted", "Дней рождения ещё нет")); return; }
  list.forEach((b) => {
    const line = tag("div", "listline");
    const date = String(b.day).padStart(2, "0") + "." + String(b.month).padStart(2, "0");
    line.innerHTML = '<span class="chip">' + date + "</span>" +
      '<span class="grow"><b>' + escapeHtml(b.name) + "</b> <span class=\"sub\">· " + escapeHtml(b.user_id) + "</span></span>";
    const del = tag("button", "btn mini danger", "✕");
    del.type = "button"; del.title = "Удалить";
    del.onclick = async () => {
      if (!confirm("Убрать день рождения " + b.name + "?")) return;
      const rr = await api("/api/birthdays/" + b.user_id + "/remove", {}, "POST");
      if (rr.status === 200 && rr.data.ok) toast("🎂 Удалено", true);
      else toast(rr.data.error ? "❌ " + rr.data.error : "❌ Ошибка", false);
      loadBirthdays();
    };
    const acts = tag("div", "row-actions");
    acts.appendChild(del);
    line.appendChild(acts);
    box.appendChild(line);
  });
}
async function addBirthday() {
  const sel = $("bd_member"), date = $("bd_date");
  if (!sel || !date) return;
  if (!sel.value) return toast("Выберите участника", false);
  const m = /^(\d{1,2})\.(\d{1,2})$/.exec(date.value.trim());
  if (!m) return toast("Дата в формате дд.мм (например, 15.08)", false);
  const r = await api("/api/birthdays", { member_id: sel.value, day: +m[1], month: +m[2] });
  if (r.status === 200 && r.data.ok) { toast("🎂 Добавлено: " + r.data.name, true); date.value = ""; loadBirthdays(); }
  else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}

/* --- временные голосовые --- */
let tvData = null;
async function loadTempVoice() {
  const r = await api("/api/tempvoice");
  if (r.status !== 200) {
    if (r.data.error) toast("❌ " + r.data.error, false);
    return;
  }
  tvData = r.data;
  const status = $("tv_status");
  if (status) {
    const trig = (r.data.triggers || []).length ? r.data.triggers.map((t) => escapeHtml(t.name || t.id)).join(", ") : "—";
    status.innerHTML = "Триггер-каналы: <b>" + trig + "</b> · категория: <b>" + escapeHtml(r.data.category_name || "—") + "</b> · комнат: <b>" + (r.data.rooms || []).length + "</b>";
  }
  renderTempVoice();
}
function renderTempVoice() {
  const box = $("tv_rooms");
  if (!box || !tvData) return;
  box.innerHTML = "";
  const rooms = tvData.rooms || [];
  if (!rooms.length) { box.appendChild(tag("div", "muted", "Активных голосовых комнат нет")); return; }
  rooms.forEach((rm) => {
    const line = tag("div", "listline");
    line.innerHTML =
      '<span class="chip" style="background:rgba(88,101,242,.16);color:#8ea1ff">🔊</span>' +
      '<span class="grow"><b>' + escapeHtml(rm.name || "(канал удалён)") + '</b> ' +
      '<span class="sub">· владелец: ' + escapeHtml(rm.owner_name || rm.owner_id) + "</span></span>";
    const acts = tag("div", "row-actions");
    const sel = document.createElement("select");
    sel.title = "Передать владельца";
    const holder = tag("option", "", "— передать —");
    holder.value = "";
    sel.appendChild(holder);
    (tvData.members || []).forEach((m) => {
      if (m.id === rm.owner_id) return;
      const o = document.createElement("option");
      o.value = m.id; o.textContent = m.name;
      sel.appendChild(o);
    });
    sel.onchange = async () => {
      if (!sel.value) return;
      const rr = await api("/api/tempvoice/" + rm.channel_id + "/transfer", { owner_id: sel.value });
      if (rr.status === 200 && rr.data.ok) toast("🔁 Владелец: " + rr.data.owner_name, true);
      else toast(rr.data.error ? "❌ " + rr.data.error : "❌ Ошибка", false);
      loadTempVoice();
    };
    const del = tag("button", "btn mini danger", "🗑 Удалить");
    del.type = "button";
    del.onclick = async () => {
      if (!confirm("Удалить комнату «" + (rm.name || rm.channel_id) + "»?")) return;
      const rr = await api("/api/tempvoice/" + rm.channel_id + "/delete", {});
      if (rr.status === 200 && rr.data.ok) toast("🔊 Комната удалена", true);
      else toast(rr.data.error ? "❌ " + rr.data.error : "❌ Ошибка", false);
      loadTempVoice();
    };
    acts.appendChild(sel);
    acts.appendChild(del);
    line.appendChild(acts);
    box.appendChild(line);
  });
}

/* --- планировщик --- */
let schData = null;
async function loadScheduler() {
  const chSel = $("sch_channel");
  if (chSel && !chSel.options.length) {
    const ch = await api("/api/bot/channels");
    const list = (ch.data && ch.data.channels) || [];
    chSel.innerHTML = "";
    list.forEach((c) => {
      const o = document.createElement("option");
      o.value = c.id; o.textContent = (c.category ? c.category + " / " : "") + c.name;
      chSel.appendChild(o);
    });
  }
  const r = await api("/api/schedule");
  schData = r.data || { upcoming: [], done: [] };
  renderScheduled();
}
function schLine(s, doneFlag) {
  const line = tag("div", "listline");
  line.innerHTML =
    '<span class="chip" style="background:rgba(35,165,90,.14);color:#23a55a">#' + s.id + "</span>" +
    '<span class="grow"><b>' + escapeHtml(s.title || s.content || "Без заголовка") + '</b> <span class="sub">' + escapeHtml(s.channel_name) + " · " + escapeHtml(String(s.content || "").slice(0, 80)) + "</span></span>" +
    '<span class="chip">' + new Date(s.send_at).toLocaleString("ru-RU") + "</span>";
  if (!doneFlag) {
    const del = tag("button", "btn mini danger", "✖");
    del.type = "button"; del.title = "Отменить";
    del.onclick = async () => {
      if (!confirm("Отменить запланированное #" + s.id + "?")) return;
      const rr = await api("/api/schedule/" + s.id, null, "DELETE");
      if (rr.status === 200 && rr.data.ok) { toast("🗑 Отменено", true); loadScheduler(); }
      else toast("❌ Не удалось отменить", false);
    };
    line.appendChild(del);
  }
  return line;
}
function renderScheduled() {
  const uBox = $("sch_upcoming"), dBox = $("sch_done");
  uBox.innerHTML = ""; dBox.innerHTML = "";
  const up = schData.upcoming || [], dn = schData.done || [];
  if (!up.length) uBox.appendChild(tag("div", "muted", "Ожидающих отправки нет"));
  up.forEach((s) => uBox.appendChild(schLine(s, false)));
  if (!dn.length) dBox.appendChild(tag("div", "muted", "Отправленных пока нет"));
  dn.forEach((s) => dBox.appendChild(schLine(s, true)));
}
async function createScheduled() {
  const channel_id = $("sch_channel").value;
  const atRaw = $("sch_at").value;
  const content = $("sch_content").value.trim();
  const title = $("sch_title").value.trim();
  const desc = $("sch_desc").value.trim();
  const color = $("sch_color").value.trim();
  if (!channel_id) return toast("Выберите канал", false);
  if (!atRaw) return toast("Укажите дату и время", false);
  if (!content && !title && !desc) return toast("Укажите текст или эмбед", false);
  const when = new Date(atRaw);
  if (isNaN(when)) return toast("Некорректная дата", false);
  if (when <= Date.now()) return toast("Дата должна быть в будущем", false);
  const embed = {};
  if (title) embed.title = title;
  if (desc) embed.description = desc;
  if (color) embed.color = color;
  const r = await api("/api/schedule", { channel_id, send_at: when.toISOString(), content, embed });
  if (r.status === 200 && r.data.ok) {
    toast("🗓 Запланировано на " + new Date(r.data.send_at).toLocaleString("ru-RU"), true);
    ["sch_at", "sch_content", "sch_title", "sch_desc", "sch_color"].forEach((id) => { $(id).value = ""; });
    loadScheduler();
  } else toast(r.data.error ? "❌ " + r.data.error : "❌ Ошибка", false);
}

/* --- бэкап --- */
async function downloadBlob(url) {
  try {
    const r = await fetch(url, { headers: { "X-Panel-Token": TOKEN } });
    if (r.status === 401) { if (PANEL_LOGIN) showLogin(); else if (TOKEN) location.reload(); return false; }
    if (!r.ok) { toast("❌ Ошибка: " + r.status, false); return false; }
    let filename = "backup";
    const cd = r.headers.get("Content-Disposition") || "";
    const m = cd.match(/filename="?([^";]+)"?/i);
    if (m) filename = m[1];
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    return true;
  } catch (e) { toast("❌ Не удалось скачать", false); return false; }
}
async function downloadBackup() {
  $("backup_info").textContent = "Готовим бэкап…";
  const ok = await downloadBlob("/api/backup");
  $("backup_info").textContent = ok ? "✅ Бэкап скачан." : "Ошибка.";
}
async function downloadBackupDb() {
  $("backup_info").textContent = "Готовим снапшот БД…";
  const ok = await downloadBlob("/api/backup/db");
  $("backup_info").textContent = ok ? "✅ Снапшот БД скачан." : "Ошибка.";
}
function loadBackup() {
  const cfg = $("backup_config");
  const modules = settingsData && settingsData.modules;
  if (modules) {
    cfg.innerHTML = '<div class="grid cols">' + Object.entries(modules).map(([k, mod]) =>
      '<div class="module-card"><h5>' + escapeHtml(MODULE_LABELS[k] || k) + '<span class="mod-env">.env</span></h5>' +
      '<div class="kv">Включён: <b>' + (mod && mod.enabled !== undefined ? (mod.enabled ? "да" : "нет") : "—") + "</b></div>" +
      "</div>"
    ).join("") + "</div>";
  } else {
    cfg.innerHTML = 'Модули загрузятся после открытия «Настройки». <button class="btn small op" type="button" onclick="switchSection(\'settings\')">Открыть</button>';
  }
}

/* --- файлы --- */
async function loadFiles() {
  const box = $("files_grid");
  box.innerHTML = '<div class="muted">Загрузка…</div>';
  const r = await api("/api/uploads");
  const list = (r.data && r.data.files) || [];
  box.innerHTML = "";
  if (list.length === 0) {
    box.appendChild(tag("div", "muted", "Загруженных файлов пока нет."));
    return;
  }
  list.forEach((f) => {
    const card = tag("div", "file-card");
    const img = document.createElement("img");
    img.className = "file-thumb"; img.loading = "lazy";
    img.src = f.url; img.alt = f.name;
    card.appendChild(img);
    const name = tag("div", "file-name", escapeHtml(f.name));
    const size = tag("div", "file-size", escapeHtml(f.size || ""));
    const actions = tag("div", "file-actions", null);
    const cp = tag("button", "btn mini", "📋 URL");
    cp.type = "button"; cp.onclick = () => copyText(f.url);
    const del = tag("button", "btn mini danger", "✖");
    del.type = "button";
    del.onclick = async () => {
      if (!confirm("Удалить файл " + f.name + "?")) return;
      const rr = await api("/api/uploads/" + encodeURIComponent(f.name), null, "DELETE");
      if (rr.status === 200 && rr.data.ok) { toast("🗑 Файл удалён", true); loadFiles(); }
      else toast("❌ Ошибка удаления", false);
    };
    actions.append(cp, del);
    card.append(name, size, actions);
    box.appendChild(card);
  });
}

/* --- логи --- */
async function loadLogs() {
  const r = await api("/api/logs?n=500");
  const logs = (r.data && r.data.logs) || [];
  const level = $("log_level").value;
  const box = $("logs_box");
  box.innerHTML = "";
  if (!r.data.ok) { box.appendChild(tag("div", "muted", "Логи недоступны")); return; }
  const filtered = level ? logs.filter((l) => l.level === level) : logs;
  if (filtered.length === 0) box.appendChild(tag("div", "muted", "Записей нет"));
  filtered.slice(-300).forEach((l) => {
    const line = tag("div", "log-line");
    const t = tag("span", "log-t", escapeHtml(l.t || ""));
    const lv = tag("span", "log-lvl " + (l.level === "ERROR" ? "err" : l.level === "WARNING" ? "warn" : "info"), escapeHtml(l.level || ""));
    const m = tag("span", "log-msg", escapeHtml((l.name ? "[" + l.name + "] " : "") + l.msg));
    line.append(t, lv, m);
    box.appendChild(line);
  });
  if (filtered.length) box.scrollTop = box.scrollHeight;
  $("logs_last").textContent = "Показано " + filtered.length + " из " + r.data.count + " · обновлено " + new Date().toLocaleTimeString("ru-RU");
}
function startLogsTimer() {
  stopLogsTimer();
  logsTimer = setInterval(() => {
    if (!$("sec-logs").classList.contains("active")) return;
    if (document.hidden) return;
    if (!$("log_auto").checked) return;
    loadLogs();
  }, 5000);
}
function stopLogsTimer() {
  if (logsTimer) { clearInterval(logsTimer); logsTimer = null; }
}

/* --- логи Discord (события) --- */
const AUDIT_CATS = {bot: "Бот", member: "Участники", message: "Сообщения", voice: "Голосовые", mod: "Модерация", general: "Общее"};
async function loadAudit() {
  const r = await api("/api/logs?n=500&audit=1");
  const logs = (r.data && r.data.logs) || [];
  const cat = $("audit_level").value;
  const box = $("audit_box");
  box.innerHTML = "";
  if (!r.data.ok) { box.appendChild(tag("div", "muted", "Логи недоступны")); return; }
  const filtered = cat ? logs.filter((l) => l.cat === cat) : logs;
  if (filtered.length === 0) box.appendChild(tag("div", "muted", "Событий нет"));
  filtered.slice(-300).forEach((l) => {
    const line = tag("div", "log-line");
    const t = tag("span", "log-t", escapeHtml(l.t || ""));
    const c = tag("span", "log-cat", escapeHtml(AUDIT_CATS[l.cat] || l.cat || ""));
    const m = tag("span", "log-msg", escapeHtml(l.msg));
    line.append(t, c, m);
    box.appendChild(line);
  });
  if (filtered.length) box.scrollTop = box.scrollHeight;
  $("audit_last").textContent = "Показано " + filtered.length + " из " + r.data.count + " · обновлено " + new Date().toLocaleTimeString("ru-RU");
}
function startAuditTimer() {
  stopAuditTimer();
  auditTimer = setInterval(() => {
    if (!$("sec-audit").classList.contains("active")) return;
    if (document.hidden) return;
    if (!$("audit_auto").checked) return;
    loadAudit();
  }, 5000);
}
function stopAuditTimer() {
  if (auditTimer) { clearInterval(auditTimer); auditTimer = null; }
}

/* --- Статистика: бары без chart.js (CSP self, без CDN) --- */

function drawBars(canvasId, items, peak) {
  const c = document.getElementById(canvasId);
  if (!c) return;
  const ctx = c.getContext("2d");
  const W = c.width, H = c.height;
  const pad = { l: 30, r: 8, t: 12, b: 22 };
  ctx.clearRect(0, 0, W, H);
  if (!items || !items.length) return;
  const n = items.length;
  const cw = (W - pad.l - pad.r) / n;
  const maxV = peak > 0 ? peak : Math.max(...items.map((i) => i.n || 0), 1);
  ctx.font = "10px system-ui, sans-serif";
  ctx.textAlign = "right";
  ctx.fillStyle = "#8a8f98";
  for (let g = 0; g <= 4; g++) {
    const v = Math.round((maxV * g) / 4);
    const y = pad.t + (H - pad.t - pad.b) - ((H - pad.t - pad.b) * g) / 4;
    ctx.strokeStyle = "#ffffff14";
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
    ctx.fillText(String(v), pad.l - 6, y + 3);
  }
  const hueIdx = (i) =>
    `hsla(${(210 + (i / (n || 1)) * 90)}, 78%, ${52 + 8 * Math.sin(i * 0.6)}%, 0.9)`;
  for (let i = 0; i < n; i++) {
    const h = ((items[i].n || 0) / maxV) * (H - pad.t - pad.b);
    const x = pad.l + i * cw + cw * 0.18;
    const w = cw * 0.64;
    const y = H - pad.b - h;
    ctx.fillStyle = hueIdx(i);
    ctx.fillRect(x, y, w, h);
    if (cw > 22 && (i % 4 === 0 || i === n - 1)) {
      ctx.textAlign = "center";
      ctx.fillStyle = "#8a8f98";
      ctx.fillText(shortLbl(items[i], i), x + w / 2, H - 8);
    }
  }
}

function shortLbl(item, i) {
  if (item.t != null) {
    const d = new Date(item.t * 1000);
    return `${String(d.getHours()).padStart(2, "0")}:00`;
  }
  if (item.d != null) return String(item.d).slice(5);
  return String(i);
}

async function loadStats() {
  const res = await fetch("/api/stats", { cache: "no-store" });
  if (res.status === 401) { showLogin(); return; }
  if (!res.ok) return;
  const data = await res.json();
  if (!data.ok) { showToast("Ошибка загрузки статистики", "err"); return; }
  const byHour = data.by_hour || [];
  const byDay = data.by_day || [];
  const hourN = byHour.reduce((s, i) => s + (i.n || 0), 0);
  const dayN = byDay.reduce((s, i) => s + (i.n || 0), 0);
  $("st_today").textContent = byDay.length ? byDay[byDay.length - 1].n : 0;
  $("st_week").textContent = byHour.slice(-24 * 7).reduce((s, i) => s + (i.n || 0), 0);
  $("st_total").textContent = data.total ?? hourN;
  const now = Date.now();
  const rate =
    byHour.length === 0
      ? "нет данных"
      : Math.round((byHour[byHour.length - 1].n || 0) / Math.max(now - byHour[byHour.length - 1].t * 1000, 1000) * 3600000);
  $("st_rate").textContent = rate;
  const maxH = Math.max(...byHour.map((i) => i.n || 0), 1);
  const maxD = Math.max(...byDay.map((i) => i.n || 0), 1);
  drawBars("chart_hours", byHour.slice(-24), maxH);
  drawBars("chart_days", byDay.slice(-14), maxD);
  connectAnalyticsSocket();
}

function applyPersistentAnalytics(payload) {
  const rows = (payload && payload.rows) || [];
  if (!rows.length) return;
  const hourly = rows.slice().reverse().map((row) => ({
    t: new Date(row.bucket).getTime() / 1000,
    n: Number(row.messages || 0),
  }));
  if (!hourly.length) return;
  const today = new Date().toISOString().slice(0, 10);
  const todayRows = hourly.filter((row) => new Date(row.t * 1000).toISOString().slice(0, 10) === today);
  $("st_today").textContent = String(todayRows.reduce((sum, row) => sum + row.n, 0));
  $("st_total").textContent = String(hourly.reduce((sum, row) => sum + row.n, 0));
  const max = Math.max(...hourly.map((row) => row.n), 1);
  drawBars("chart_hours", hourly.slice(-24), max);
}

function connectAnalyticsSocket() {
  if (analyticsSocket && analyticsSocket.readyState <= 1) return;
  if (!window.WebSocket || !TOKEN) return;
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  analyticsSocket = new WebSocket(`${scheme}//${location.host}/ws/analytics`);
  analyticsSocket.onopen = () => analyticsSocket.send(JSON.stringify({ token: TOKEN }));
  analyticsSocket.onmessage = (event) => {
    try {
      const message = JSON.parse(event.data);
      if (message.type === "analytics") applyPersistentAnalytics(message.data);
    } catch (_) {}
  };
  analyticsSocket.onclose = () => {
    analyticsSocket = null;
    clearTimeout(analyticsReconnectTimer);
    analyticsReconnectTimer = setTimeout(connectAnalyticsSocket, 10000);
  };
}


applyTheme();
buildAccents();
$("btn_login").onclick = doLogin;
$("login_pw").addEventListener("keydown", (e) => { if (e.key === "Enter") doLogin(); });
if ($("btn_oauth")) {
  $("btn_oauth").style.display = PANEL_OAUTH ? "block" : "none";
  $("btn_oauth").onclick = () => { location.href = "/oauth/discord"; };
}

async function bootInit() {
  if (TOKEN) {
    const session = await api("/api/session");
    if (session.data && session.data.csrf) {
      CSRF_TOKEN = session.data.csrf;
      localStorage.setItem("panel-csrf", CSRF_TOKEN);
    }
  }
  renderEmbeds();
  renderButtons();
  renderPreview();
  setMode("webhook");
  loadTestChannels();
  loadOverview();
  startOverviewTimer();
  if (settingsData) renderSettings();
}
if (PANEL_LOGIN && !TOKEN) { showLogin(); startOverviewTimer(); }
else bootInit();
