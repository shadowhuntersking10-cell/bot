/* VYRON admin panel (inside the SPA — works on the website and in the Telegram Web App).
   Every call is authorized server-side (require_admin); the UI is only a convenience. */
import {
  api, state, esc, safeUrl, money, som, t, icon, toast, modal, loading, empty, notice, navigate,
  errMsg, withBusy, dateTime, statusPill, confirmDialog, copyText, FALLBACK_IMG,
} from "./core.js";
import { renderShell } from "./shell.js";

const NAV = [
  ["g_overview"],
  ["dashboard", "dashboard", "a_dashboard"],
  ["health", "pulse", "a_health"],
  ["g_catalog"],
  ["games", "games", "a_games"],
  ["products", "box", "a_products"],
  ["variants", "layers", "a_variants"],
  ["pricing", "percent", "a_pricing"],
  ["coupons", "tag", "a_coupons"],
  ["promotions", "gift", "a_promotions"],
  ["g_sales"],
  ["orders", "orders", "a_orders"],
  ["topups", "wallet", "a_topups"],
  ["wallet", "exchange", "a_wallet_tx"],
  ["payments", "card", "a_payments"],
  ["fulfillments", "truck", "a_fulfillments"],
  ["market", "store", "a_market"],
  ["g_people"],
  ["users", "users", "a_users"],
  ["broadcast", "send", "a_broadcast"],
  ["g_settings"],
  ["integrations", "plug", "a_integrations"],
  ["payerpin", "box", "a_payerpin"],
  ["branding", "palette", "a_branding"],
  ["media", "image", "a_media"],
  ["topup-settings", "coins", "a_topup_settings"],
  ["telegram", "telegram", "a_telegram"],
  ["audit", "file", "a_audit"],
  ["security", "shield", "a_security"],
];

export async function renderAdmin(view, parts, params) {
  if (!state.user) { navigate("#/login?next=" + encodeURIComponent(location.hash)); return; }
  if (!state.user.is_admin) {
    view.innerHTML = `<div class="card">${empty("lock", t("forbidden_title"), t("forbidden_d"))}</div>`;
    return;
  }
  const section = parts[1] || "dashboard";
  view.innerHTML = `<div class="admin">
    <nav class="card admin-nav">${NAV.map((n) => n.length === 1
      ? `<div class="grp">${esc(t(n[0]))}</div>`
      : `<a href="#/admin/${n[0]}" class="${n[0] === section ? "active" : ""}">${icon(n[1], "sm")} ${esc(t(n[2]))}</a>`).join("")}</nav>
    <div id="a-main" class="stack">${loading()}</div></div>`;
  const main = view.querySelector("#a-main");
  const activeLink = view.querySelector(".admin-nav a.active");
  if (activeLink && activeLink.scrollIntoView && window.innerWidth < 960) activeLink.scrollIntoView({ inline: "center", block: "nearest" });
  const fn = SECTIONS[section];
  if (!fn) { main.innerHTML = empty("info", t("nothing_found")); return; }
  try { await fn(main, params); } catch (e) { main.innerHTML = notice(esc(errMsg(e)), "err"); }
}

/* ============================ helpers ============================ */
function head(title, extra = "") {
  return `<div class="row between wrap"><h2 style="margin:0">${esc(title)}</h2><div class="row wrap">${extra}</div></div>`;
}
function yes(v) { return v ? `<span class="pill ok">${icon("check", "sm")}</span>` : `<span class="pill">—</span>`; }
function cfgPill(ok) { return ok ? `<span class="pill ok">${esc(t("configured"))}</span>` : `<span class="pill warn">${esc(t("not_configured"))}</span>`; }

function table(cols, rows, rowFn) {
  if (!rows.length) return `<div class="card">${empty("search", t("nothing_found"))}</div>`;
  return `<div class="card" style="padding:6px"><div class="table-wrap"><table class="tbl"><thead><tr>${cols.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map(rowFn).join("")}</tbody></table></div></div>`;
}
function pager(data, onPage) {
  const pages = Math.max(1, Math.ceil((data.total || 0) / (data.page_size || 20)));
  const id = "pg" + Math.random().toString(36).slice(2, 8);
  setTimeout(() => {
    const el = document.getElementById(id);
    if (!el) return;
    el.querySelectorAll("[data-p]").forEach((b) => b.addEventListener("click", () => onPage(Number(b.dataset.p))));
  });
  return `<div class="pager" id="${id}"><span class="small muted">${data.total || 0} · ${data.page}/${pages}</span>
    <button class="btn sm" data-p="${data.page - 1}" ${data.page <= 1 ? "disabled" : ""}>${icon("back", "sm")}</button>
    <button class="btn sm" data-p="${data.page + 1}" ${data.page >= pages ? "disabled" : ""}>${icon("right", "sm")}</button></div>`;
}

/* Form builder. field: {name,label,type,options,help,required,full} ; money = so'm in UI, minor units in API */
function fieldHtml(f, value) {
  const label = `<span>${esc(f.label)}${f.required ? " *" : ""}</span>`;
  const v = value ?? f.default ?? "";
  const full = f.full || ["textarea", "image", "json"].includes(f.type) ? ' style="grid-column:1/-1"' : "";
  switch (f.type) {
    case "checkbox":
      return `<label class="check"${full}><input type="checkbox" name="${f.name}" ${v ? "checked" : ""}> ${esc(f.label)}</label>`;
    case "select":
      return `<label class="field"${full}>${label}<select class="input" name="${f.name}">${f.options.map(([ov, ol]) => `<option value="${esc(ov)}" ${String(ov) === String(v ?? "") ? "selected" : ""}>${esc(ol)}</option>`).join("")}</select>${f.help ? `<p class="help">${esc(f.help)}</p>` : ""}</label>`;
    case "textarea":
      return `<label class="field"${full}>${label}<textarea class="input" name="${f.name}" maxlength="${f.max || 4000}">${esc(v)}</textarea>${f.help ? `<p class="help">${esc(f.help)}</p>` : ""}</label>`;
    case "json":
      return `<label class="field"${full}>${label}<textarea class="input mono" name="${f.name}" data-json>${esc(v ? JSON.stringify(v, null, 2) : "")}</textarea>${f.help ? `<p class="help">${esc(f.help)}</p>` : ""}</label>`;
    case "image":
      return `<label class="field"${full}>${label}<div class="img-pick"><img src="${safeUrl(v, FALLBACK_IMG)}" alt="" data-prev="${f.name}">
        <input class="input" name="${f.name}" value="${esc(v)}" placeholder="/uploads/... or https://..." data-imgin>
        <button type="button" class="btn sm" data-upload="${f.name}" data-kind="${f.kind || "media"}" title="${esc(t("upload"))}">${icon("upload", "sm")}</button>
        <button type="button" class="btn sm" data-library="${f.name}" title="${esc(t("a_media"))}">${icon("image", "sm")}</button></div>
        ${f.help ? `<p class="help">${esc(f.help)}</p>` : ""}</label>`;
    case "money":
      return `<label class="field"${full}>${label}<input class="input" type="number" step="1" name="${f.name}" data-money value="${v === "" || v === null ? "" : Math.round(Number(v) / 100)}" ${f.required ? "required" : ""}>${f.help ? `<p class="help">${esc(f.help)}</p>` : ""}</label>`;
    case "number":
      return `<label class="field"${full}>${label}<input class="input" type="number" step="${f.step || 1}" name="${f.name}" data-num value="${esc(v)}" ${f.required ? "required" : ""}>${f.help ? `<p class="help">${esc(f.help)}</p>` : ""}</label>`;
    case "datetime": {
      const val = v ? String(v).slice(0, 16) : "";
      return `<label class="field"${full}>${label}<input class="input" type="datetime-local" name="${f.name}" value="${esc(val)}"></label>`;
    }
    default:
      return `<label class="field"${full}>${label}<input class="input" type="${f.type || "text"}" name="${f.name}" value="${esc(v)}" ${f.required ? "required" : ""} maxlength="${f.max || 512}">${f.help ? `<p class="help">${esc(f.help)}</p>` : ""}</label>`;
  }
}
function readForm(form, fields) {
  const out = {};
  for (const f of fields) {
    const el = form.elements[f.name];
    if (!el) continue;
    if (f.type === "checkbox") out[f.name] = el.checked;
    else if (f.type === "money") out[f.name] = el.value === "" ? null : Math.round(Number(el.value) * 100);
    else if (f.type === "number") out[f.name] = el.value === "" ? null : Number(el.value);
    else if (f.type === "json") {
      const raw = el.value.trim();
      if (!raw) out[f.name] = null;
      else { try { out[f.name] = JSON.parse(raw); } catch (e) { throw new Error(`${f.label}: JSON`); } }
    } else if (f.type === "datetime") out[f.name] = el.value ? el.value + ":00" : null;
    else out[f.name] = el.value.trim() === "" && f.nullable !== false ? (f.emptyAs !== undefined ? f.emptyAs : null) : el.value.trim();
  }
  return out;
}
function bindImageInputs(root) {
  root.querySelectorAll("[data-imgin]").forEach((inp) => inp.addEventListener("input", () => {
    const prev = root.querySelector(`[data-prev="${inp.name}"]`);
    if (prev) prev.src = /^(https?:\/\/|\/)/.test(inp.value) ? inp.value : FALLBACK_IMG;
  }));
  root.querySelectorAll("[data-upload]").forEach((btn) => btn.addEventListener("click", () => {
    const picker = document.createElement("input");
    picker.type = "file";
    picker.accept = "image/png,image/jpeg,image/webp,image/gif,image/x-icon";
    picker.onchange = () => withBusy(btn, async () => {
      const file = picker.files[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      form.append("kind", btn.dataset.kind || "media");
      try {
        const res = await api("/api/admin/uploads", { method: "POST", form });
        const inp = root.querySelector(`[name="${btn.dataset.upload}"]`);
        inp.value = res.url;
        inp.dispatchEvent(new Event("input"));
        toast(t("uploaded"));
      } catch (e) { toast(errMsg(e), "err"); }
    });
    picker.click();
  }));
  root.querySelectorAll("[data-library]").forEach((btn) => btn.addEventListener("click", async () => {
    const res = await api("/api/admin/media").catch(() => ({ items: [] }));
    const builtIn = ["uc", "ff-diamonds", "ml-diamonds", "robux", "coc-gems", "cr-gems", "bs-gems", "so2-gold", "primogems", "tg-stars"]
      .map((n) => ({ url: `/assets/img/currency/${n}.webp`, name: n }));
    const items = [...res.items, ...builtIn, { url: "/assets/img/logo.svg", name: "logo.svg" }];
    const m = modal(t("a_media"), `<div class="media-grid">${items.map((x) => `<button type="button" class="card m" style="cursor:pointer;border:0" data-pick="${esc(x.url)}"><img src="${safeUrl(x.url)}" alt=""><div class="tiny muted ellipsis">${esc(x.name)}</div></button>`).join("")}</div>`, { wide: true });
    m.el.querySelectorAll("[data-pick]").forEach((b) => b.addEventListener("click", () => {
      const inp = root.querySelector(`[name="${btn.dataset.library}"]`);
      inp.value = b.dataset.pick;
      inp.dispatchEvent(new Event("input"));
      m.close();
    }));
  }));
}
function formModal(title, fields, values, onSubmit, { wide = true } = {}) {
  const m = modal(title, `<form class="stack"><div class="grid g2">${fields.map((f) => fieldHtml(f, values ? values[f.name] : undefined)).join("")}</div>
    <div class="row" style="justify-content:flex-end"><button type="button" class="btn" data-close>${esc(t("cancel"))}</button><button class="btn primary">${icon("check", "sm")} ${esc(t("save"))}</button></div></form>`, { wide });
  const form = m.el.querySelector("form");
  bindImageInputs(form);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    withBusy(form.querySelector(".btn.primary"), async () => {
      let data;
      try { data = readForm(form, fields); } catch (err) { toast(err.message, "err"); return; }
      try { await onSubmit(data); m.close(); toast(t("saved")); } catch (err) { toast(errMsg(err), "err"); }
    });
  });
  return m;
}

/* ============================ sections ============================ */
const SECTIONS = {};

SECTIONS.dashboard = async (main) => {
  const [d, a] = await Promise.all([api("/api/admin/dashboard"), api("/api/admin/analytics?days=14")]);
  const s = d.stats;
  const max = Math.max(1, ...a.daily.map((x) => x.revenue));
  const ints = d.integrations;
  main.innerHTML = `${head(t("a_dashboard"))}
    ${!ints.payerpin || !ints.hamyon || !ints.telegram ? notice(`${esc(t("setup_incomplete"))} <a href="#/admin/integrations">${esc(t("a_integrations"))}</a>`, "", "plug") : ""}
    <div class="grid g4">
      ${[["revenue_total", money(s.revenue_total)], ["revenue_24h", money(s.revenue_24h)], ["profit_total", money(s.profit_total)], ["users", s.users],
         ["orders_total", s.orders_total], ["orders_processing", s.orders_processing], ["orders_failed", s.orders_failed], ["orders_pending", s.orders_pending],
         ["products_active", `${s.products_active} / ${s.products_total}`], ["fulfillments_pending", s.fulfillments_pending],
         ["total_customer_balance", money(s.total_customer_balance || 0)], ["total_topups", money(s.total_topups || 0)]]
        .map(([k, v]) => `<div class="card stat"><div class="l">${esc(t("s_" + k))}</div><div class="v">${v}</div></div>`).join("")}
    </div>
    <div class="grid g2">
      <div class="card"><h3>${esc(t("revenue_14d"))}</h3><div class="bars">${a.daily.map((x) => `<div title="${esc(x.date)}: ${money(x.revenue)} · ${x.orders}" style="height:${Math.max(2, (x.revenue / max) * 100)}%"></div>`).join("")}</div>
        <div class="row between tiny muted"><span>${esc(a.daily[0] ? a.daily[0].date : "")}</span><span>${esc(a.daily.length ? a.daily[a.daily.length - 1].date : "")}</span></div></div>
      <div class="card"><h3>${esc(t("integrations"))}</h3><div class="kv">
        <span>Payerpin</span><span>${cfgPill(ints.payerpin)}</span><span>Hamyon</span><span>${cfgPill(ints.hamyon)}</span>
        <span>Telegram</span><span>${cfgPill(ints.telegram)}</span><span>PUBLIC_BASE_URL</span><span>${cfgPill(ints.public_base_url)}</span></div></div>
    </div>
    <div class="card"><h3>${esc(t("recent_orders"))}</h3>
      ${d.recent_orders.length ? `<div class="list">${d.recent_orders.map((o) => `<a class="li" href="#/admin/orders?open=${o.id}" style="color:var(--text)"><div class="grow"><b class="mono small">${esc(o.order_number)}</b><div class="tiny muted">${esc(o.items.map((i) => i.variant_name).join(", "))}</div></div><b>${money(o.total)}</b>${statusPill(o.status)}</a>`).join("")}</div>` : `<p class="muted">${esc(t("no_orders"))}</p>`}
    </div>`;
};

SECTIONS.health = async (main) => {
  const h = await api("/api/admin/health");
  main.innerHTML = `${head(t("a_health"), `<button class="btn sm" data-r>${icon("refresh", "sm")}</button>`)}
    <div class="grid g2">${Object.entries(h.checks).map(([k, c]) => `<div class="card row"><div class="tx-ic" style="color:${/Connected|Configured/.test(c.status) ? "var(--ok)" : "var(--warn)"}">${icon(/Connected|Configured/.test(c.status) ? "checkCircle" : "alert")}</div>
      <div class="grow"><b>${esc(k)}</b><div class="tiny muted ellipsis">${esc(c.detail || "")}</div></div><span class="pill ${/Connected|Configured/.test(c.status) ? "ok" : "warn"}">${esc(c.status)}</span></div>`).join("")}</div>
    <p class="tiny muted">${esc(h.env)} · ${esc(h.time)}</p>`;
  main.querySelector("[data-r]").addEventListener("click", () => SECTIONS.health(main));
};

/* ---------- games ---------- */
const CATEGORY_OPTS = [["mobile", "Mobile"], ["pc", "PC"], ["console", "Console"], ["gift-cards", "Gift cards"], ["other", "Other"]];
function gameFields() {
  return [
    { name: "name", label: t("f_name") + " (EN)", required: true },
    { name: "slug", label: "Slug", help: t("slug_help") },
    { name: "name_uz", label: t("f_name") + " (UZ)" },
    { name: "name_ru", label: t("f_name") + " (RU)" },
    { name: "category", label: t("f_category"), type: "select", options: CATEGORY_OPTS },
    { name: "currency_label", label: t("f_currency_label"), help: "UC, Diamonds, Robux…" },
    { name: "icon_url", label: t("f_icon"), type: "image", kind: "game" },
    { name: "logo_url", label: t("f_logo"), type: "image", kind: "game" },
    { name: "banner_url", label: t("f_banner"), type: "image", kind: "banner" },
    { name: "description_uz", label: t("description") + " (UZ)", type: "textarea" },
    { name: "description_en", label: t("description") + " (EN)", type: "textarea" },
    { name: "description_ru", label: t("description") + " (RU)", type: "textarea" },
    { name: "sort_order", label: t("f_sort"), type: "number" },
    { name: "featured", label: t("f_featured"), type: "checkbox" },
    { name: "active", label: t("f_active"), type: "checkbox", default: true },
  ];
}
SECTIONS.games = async (main, params, page = 1, search = "") => {
  const d = await api(`/api/admin/games?page=${page}&page_size=30${search ? "&search=" + encodeURIComponent(search) : ""}`);
  main.innerHTML = `${head(t("a_games"), `<input class="input" style="min-height:36px;width:180px" placeholder="${esc(t("search"))}" value="${esc(search)}" data-s><button class="btn sm primary" data-new>${icon("plus", "sm")} ${esc(t("add"))}</button>`)}
    ${table(["", t("f_name"), t("f_category"), t("f_currency_label"), t("a_products"), t("f_featured"), t("f_active"), ""], d.items, (g) => `<tr>
      <td><img class="thumb-sm" src="${safeUrl(g.icon_url, FALLBACK_IMG)}" alt=""></td><td><b>${esc(g.name)}</b><div class="tiny muted">${esc(g.slug)}${g.supplier_game_key ? " · " + esc(g.supplier_game_key) : ""}</div></td>
      <td>${esc(g.category || "")}</td><td>${esc(g.currency_label || "")}</td><td>${g.products}</td><td>${yes(g.featured)}</td><td>${yes(g.active)}</td>
      <td><button class="btn sm" data-edit="${g.id}">${icon("edit", "sm")}</button></td></tr>`)}
    ${pager(d, (p) => SECTIONS.games(main, params, p, search))}`;
  main.querySelector("[data-s]").addEventListener("change", (e) => SECTIONS.games(main, params, 1, e.target.value.trim()));
  main.querySelector("[data-new]").addEventListener("click", () => formModal(t("add"), gameFields(), { active: true, category: "mobile" }, async (data) => {
    await api("/api/admin/games", { method: "POST", body: data }); SECTIONS.games(main, params, page, search);
  }));
  main.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    const g = d.items.find((x) => String(x.id) === b.dataset.edit);
    formModal(g.name, gameFields(), g, async (data) => {
      await api(`/api/admin/games/${g.id}`, { method: "PATCH", body: data }); SECTIONS.games(main, params, page, search);
    });
  }));
};

/* ---------- products ---------- */
async function gameOptions() {
  const r = await api("/api/admin/games?page_size=100");
  return r.items.map((g) => [g.id, g.name]);
}
function productFields(gopts) {
  return [
    { name: "name", label: t("f_name") + " (EN)", required: true },
    { name: "game_id", label: t("game"), type: "select", options: gopts },
    { name: "name_uz", label: t("f_name") + " (UZ)" },
    { name: "name_ru", label: t("f_name") + " (RU)" },
    { name: "product_type", label: t("f_type"), type: "select", options: [["TOPUP", "Top-up"], ["GIFT_CARD", "Gift card"], ["DIGITAL", "Digital"], ["ACCOUNT", "Account"]] },
    { name: "fulfillment_type", label: t("f_fulfillment"), type: "select", options: [["AUTO", "AUTO (Payerpin)"], ["MANUAL", "MANUAL"], ["ACCOUNT", "ACCOUNT"]] },
    { name: "image_url", label: t("f_image"), type: "image", kind: "product", help: t("image_help") },
    { name: "margin_percent", label: t("f_margin_percent"), type: "number", help: t("margin_override_help") },
    { name: "margin_fixed", label: t("f_margin_fixed"), type: "money" },
    { name: "required_fields", label: t("f_required_fields"), type: "json", help: '{"fields":[{"key":"player_id","label":"Player ID","required":true}]}' },
    { name: "description_uz", label: t("description") + " (UZ)", type: "textarea" },
    { name: "description_en", label: t("description") + " (EN)", type: "textarea" },
    { name: "description_ru", label: t("description") + " (RU)", type: "textarea" },
    { name: "visibility", label: t("f_visible"), type: "checkbox", default: true },
    { name: "active", label: t("f_active"), type: "checkbox", default: true },
  ];
}
SECTIONS.products = async (main, params, page = 1, search = "") => {
  const [d, gopts] = await Promise.all([
    api(`/api/admin/products?page=${page}&page_size=30${search ? "&search=" + encodeURIComponent(search) : ""}`), gameOptions()]);
  main.innerHTML = `${head(t("a_products"), `<input class="input" style="min-height:36px;width:180px" placeholder="${esc(t("search"))}" value="${esc(search)}" data-s><button class="btn sm primary" data-new>${icon("plus", "sm")} ${esc(t("add"))}</button>`)}
    ${notice(esc(t("products_note")), "info", "info")}
    ${table(["", t("f_name"), t("game"), t("f_type"), t("a_variants"), t("f_active"), ""], d.items, (p) => `<tr>
      <td><img class="thumb-sm" src="${safeUrl(p.image_url, FALLBACK_IMG)}" alt=""></td><td><b>${esc(p.name)}</b><div class="tiny muted">${esc(p.slug)}</div></td>
      <td>${esc(p.game || "")}</td><td>${esc(p.product_type)} · ${esc(p.fulfillment_type)}</td><td><a href="#/admin/variants?product=${p.id}">${p.variants}</a></td><td>${yes(p.active && p.visibility)}</td>
      <td><button class="btn sm" data-edit="${p.id}">${icon("edit", "sm")}</button></td></tr>`)}
    ${pager(d, (pg) => SECTIONS.products(main, params, pg, search))}`;
  main.querySelector("[data-s]").addEventListener("change", (e) => SECTIONS.products(main, params, 1, e.target.value.trim()));
  main.querySelector("[data-new]").addEventListener("click", () => formModal(t("add"), productFields(gopts), { active: true, visibility: true }, async (data) => {
    await api("/api/admin/products", { method: "POST", body: data }); SECTIONS.products(main, params, page, search);
  }));
  main.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    const p = d.items.find((x) => String(x.id) === b.dataset.edit);
    formModal(p.name, productFields(gopts), p, async (data) => {
      await api(`/api/admin/products/${p.id}`, { method: "PATCH", body: data }); SECTIONS.products(main, params, page, search);
    });
  }));
};

/* ---------- variants ---------- */
function variantFields(isNew) {
  return [
    { name: "name", label: t("f_name") + " (EN)", required: true },
    { name: "amount_label", label: t("f_amount_label"), help: "60 UC, 100 Diamonds…" },
    { name: "name_uz", label: t("f_name") + " (UZ)" },
    { name: "name_ru", label: t("f_name") + " (RU)" },
    ...(isNew ? [
      { name: "supplier_product_id", label: "Supplier product ID", help: t("supplier_map_help") },
      { name: "supplier_cost", label: t("f_supplier_cost"), type: "money" },
    ] : []),
    { name: "margin_percent", label: t("f_margin_percent"), type: "number", help: t("margin_override_help") },
    { name: "margin_fixed", label: t("f_margin_fixed"), type: "money" },
    { name: "region", label: t("f_region") },
    { name: "sort_order", label: t("f_sort"), type: "number" },
    { name: "in_stock", label: t("f_in_stock"), type: "checkbox", default: true },
    { name: "active", label: t("f_active"), type: "checkbox", default: true },
  ];
}
SECTIONS.variants = async (main, params, page = 1, search = "") => {
  const pid = params.product || "";
  const d = await api(`/api/admin/variants?page=${page}&page_size=40${pid ? "&product_id=" + pid : ""}${search ? "&search=" + encodeURIComponent(search) : ""}`);
  main.innerHTML = `${head(t("a_variants"), `<input class="input" style="min-height:36px;width:180px" placeholder="${esc(t("search"))}" value="${esc(search)}" data-s>${pid ? `<button class="btn sm primary" data-new>${icon("plus", "sm")} ${esc(t("add"))}</button>` : ""}`)}
    ${notice(esc(t("variants_note")), "info", "shield")}
    ${table([t("f_name"), t("a_products"), t("f_supplier_cost"), t("price"), t("profit"), "Map", t("f_active"), ""], d.items, (v) => `<tr>
      <td><b>${esc(v.amount_label || v.name)}</b><div class="tiny muted">#${v.id}${v.region ? " · " + esc(v.region) : ""}</div></td><td class="small">${esc(v.product || "")}</td>
      <td>${v.supplier_cost === null ? "—" : money(v.supplier_cost)}</td><td><b>${v.price === null ? `<span class="pill warn">${esc(t("not_available"))}</span>` : money(v.price)}</b></td>
      <td>${v.profit === null || v.profit === undefined ? "—" : money(v.profit)}</td><td>${v.mapped ? `<span class="pill ok">Payerpin</span>` : `<span class="pill">—</span>`}</td>
      <td>${yes(v.active && v.in_stock)}</td><td><button class="btn sm" data-edit="${v.id}">${icon("edit", "sm")}</button></td></tr>`)}
    ${pager(d, (pg) => SECTIONS.variants(main, params, pg, search))}`;
  main.querySelector("[data-s]").addEventListener("change", (e) => SECTIONS.variants(main, params, 1, e.target.value.trim()));
  const nb = main.querySelector("[data-new]");
  if (nb) nb.addEventListener("click", () => formModal(t("add"), variantFields(true), { active: true, in_stock: true }, async (data) => {
    await api("/api/admin/variants", { method: "POST", body: { ...data, product_id: Number(pid) } }); SECTIONS.variants(main, params, page, search);
  }));
  main.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    const v = d.items.find((x) => String(x.id) === b.dataset.edit);
    formModal(v.name, variantFields(false), v, async (data) => {
      await api(`/api/admin/variants/${v.id}`, { method: "PATCH", body: { ...data, product_id: v.product_id } }); SECTIONS.variants(main, params, page, search);
    });
  }));
};

/* ---------- pricing ---------- */
SECTIONS.pricing = async (main) => {
  const d = (await api("/api/admin/pricing")).pricing;
  const fields = [
    { name: "margin_percent", label: t("f_margin_percent"), type: "number" },
    { name: "margin_fixed", label: t("f_margin_fixed"), type: "money" },
    { name: "payment_fee_percent", label: t("f_fee_percent"), type: "number" },
    { name: "payment_fee_fixed", label: t("f_fee_fixed"), type: "money" },
    { name: "min_margin_percent", label: t("f_min_margin"), type: "number" },
    { name: "allow_below_min_margin", label: t("f_allow_below"), type: "checkbox" },
  ];
  main.innerHTML = `${head(t("a_pricing"))}
    ${notice(esc(t("pricing_formula")), "info", "percent")}
    <form class="card stack"><div class="grid g2">${fields.map((f) => fieldHtml(f, d[f.name])).join("")}</div>
    <button class="btn primary">${icon("check", "sm")} ${esc(t("save_recalc"))}</button></form>`;
  const form = main.querySelector("form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    withBusy(form.querySelector("button"), async () => {
      try { await api("/api/admin/pricing", { method: "PUT", body: readForm(form, fields) }); toast(t("saved")); } catch (err) { toast(errMsg(err), "err"); }
    });
  });
};

/* ---------- coupons ---------- */
function couponFields(isNew) {
  return [
    { name: "code", label: t("coupon_code"), required: true, ...(isNew ? {} : { type: "text" }) },
    { name: "coupon_type", label: t("f_type"), type: "select", options: [["PERCENT", "%"], ["FIXED", t("fixed_sum")]] },
    { name: "value", label: t("f_value"), type: "number", required: true, help: t("coupon_value_help") },
    { name: "min_order_amount", label: t("f_min_order"), type: "money", default: 0 },
    { name: "max_uses", label: t("f_max_uses"), type: "number" },
    { name: "per_user_limit", label: t("f_per_user"), type: "number", default: 1 },
    { name: "expires_at", label: t("f_expires"), type: "datetime" },
    { name: "allow_below_margin", label: t("f_allow_below"), type: "checkbox" },
    { name: "active", label: t("f_active"), type: "checkbox", default: true },
  ];
}
SECTIONS.coupons = async (main) => {
  const d = await api("/api/admin/coupons");
  main.innerHTML = `${head(t("a_coupons"), `<button class="btn sm primary" data-new>${icon("plus", "sm")} ${esc(t("add"))}</button>`)}
    ${table([t("coupon_code"), t("f_value"), t("f_min_order"), t("f_uses"), t("f_expires"), t("f_active"), ""], d.items, (c) => `<tr>
      <td class="mono"><b>${esc(c.code)}</b></td><td>${c.coupon_type === "PERCENT" ? c.value + "%" : money(c.value)}</td><td>${money(c.min_order_amount)}</td>
      <td>${c.used_count}${c.max_uses ? " / " + c.max_uses : ""}</td><td class="small">${c.expires_at ? dateTime(c.expires_at) : "—"}</td><td>${yes(c.active)}</td>
      <td><button class="btn sm" data-edit="${c.id}">${icon("edit", "sm")}</button></td></tr>`)}`;
  const fix = (data) => { if (data.coupon_type === "FIXED") data.value = Math.round(Number(data.value || 0) * 100); data.min_order_amount = data.min_order_amount || 0; data.per_user_limit = data.per_user_limit || 1; return data; };
  main.querySelector("[data-new]").addEventListener("click", () => formModal(t("add"), couponFields(true), { active: true, coupon_type: "PERCENT", per_user_limit: 1 }, async (data) => {
    await api("/api/admin/coupons", { method: "POST", body: fix(data) }); SECTIONS.coupons(main);
  }));
  main.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    const c = d.items.find((x) => String(x.id) === b.dataset.edit);
    const vals = { ...c, value: c.coupon_type === "FIXED" ? Math.round(c.value / 100) : c.value };
    formModal(c.code, couponFields(false), vals, async (data) => {
      await api(`/api/admin/coupons/${c.id}`, { method: "PATCH", body: fix({ ...data, code: c.code, coupon_type: c.coupon_type }) }); SECTIONS.coupons(main);
    });
  }));
};

/* ---------- promotions ---------- */
function promoFields(gopts) {
  return [
    { name: "title", label: t("title") + " (EN)", required: true },
    { name: "title_uz", label: t("title") + " (UZ)" },
    { name: "title_ru", label: t("title") + " (RU)" },
    { name: "coupon_code", label: t("coupon_code") },
    { name: "game_id", label: t("game"), type: "select", options: [["", "—"], ...gopts] },
    { name: "image_url", label: t("f_image"), type: "image", kind: "promo" },
    { name: "description_uz", label: t("description") + " (UZ)", type: "textarea" },
    { name: "description_en", label: t("description") + " (EN)", type: "textarea" },
    { name: "description_ru", label: t("description") + " (RU)", type: "textarea" },
    { name: "starts_at", label: t("f_starts"), type: "datetime" },
    { name: "ends_at", label: t("f_ends"), type: "datetime" },
    { name: "active", label: t("f_active"), type: "checkbox", default: true },
  ];
}
SECTIONS.promotions = async (main) => {
  const [d, gopts] = await Promise.all([api("/api/admin/promotions"), gameOptions()]);
  main.innerHTML = `${head(t("a_promotions"), `<button class="btn sm primary" data-new>${icon("plus", "sm")} ${esc(t("add"))}</button>`)}
    ${table(["", t("title"), t("coupon_code"), t("f_ends"), t("f_active"), ""], d.items, (p) => `<tr>
      <td><img class="thumb-sm" src="${safeUrl(p.image_url, FALLBACK_IMG)}" alt=""></td><td><b>${esc(p.title)}</b></td><td class="mono">${esc(p.coupon_code || "—")}</td>
      <td class="small">${p.ends_at ? dateTime(p.ends_at) : "—"}</td><td>${yes(p.active)}</td><td><button class="btn sm" data-edit="${p.id}">${icon("edit", "sm")}</button></td></tr>`)}`;
  const fix = (data) => { data.game_id = data.game_id ? Number(data.game_id) : null; return data; };
  main.querySelector("[data-new]").addEventListener("click", () => formModal(t("add"), promoFields(gopts), { active: true }, async (data) => {
    await api("/api/admin/promotions", { method: "POST", body: fix(data) }); SECTIONS.promotions(main);
  }));
  main.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    const p = d.items.find((x) => String(x.id) === b.dataset.edit);
    formModal(p.title, promoFields(gopts), p, async (data) => {
      await api(`/api/admin/promotions/${p.id}`, { method: "PATCH", body: fix(data) }); SECTIONS.promotions(main);
    });
  }));
};

/* ---------- orders ---------- */
const O_STATUSES = ["", "PENDING_PAYMENT", "PAID", "FULFILLMENT_PENDING", "SUPPLIER_PROCESSING", "COMPLETED", "FAILED", "REFUNDED", "CANCELLED"];
SECTIONS.orders = async (main, params, page = 1, status = "", search = "") => {
  const d = await api(`/api/admin/orders?page=${page}&page_size=25${status ? "&status=" + status : ""}${search ? "&search=" + encodeURIComponent(search) : ""}`);
  main.innerHTML = `${head(t("a_orders"), `<input class="input" style="min-height:36px;width:180px" placeholder="VYR-..." value="${esc(search)}" data-s>
      <select class="input" style="min-height:36px;width:auto" data-f>${O_STATUSES.map((s) => `<option value="${s}" ${s === status ? "selected" : ""}>${esc(s ? t("st_" + s) : t("all"))}</option>`).join("")}</select>`)}
    ${table([t("order"), t("user"), t("total"), t("profit"), t("status"), t("created"), ""], d.items, (o) => `<tr>
      <td class="mono small"><b>${esc(o.order_number)}</b><div class="tiny muted">${esc(o.items.map((i) => i.variant_name).join(", "))}</div></td><td>${esc(o.user || "")}</td>
      <td><b>${money(o.total)}</b></td><td>${money(o.profit)}</td><td>${statusPill(o.status)}</td><td class="small">${dateTime(o.created_at)}</td>
      <td><button class="btn sm" data-open="${o.id}">${icon("eye", "sm")}</button></td></tr>`)}
    ${pager(d, (p) => SECTIONS.orders(main, params, p, status, search))}`;
  main.querySelector("[data-f]").addEventListener("change", (e) => SECTIONS.orders(main, params, 1, e.target.value, search));
  main.querySelector("[data-s]").addEventListener("change", (e) => SECTIONS.orders(main, params, 1, status, e.target.value.trim()));
  main.querySelectorAll("[data-open]").forEach((b) => b.addEventListener("click", () => orderModal(b.dataset.open, () => SECTIONS.orders(main, params, page, status, search))));
  if (params.open) { const id = params.open; delete params.open; orderModal(id, () => SECTIONS.orders(main, params, page, status, search)); }
};
async function orderModal(id, reload) {
  const o = (await api(`/api/admin/orders/${id}`)).order;
  const canRetry = ["FAILED", "FULFILLMENT_PENDING", "PAID"].includes(o.status);
  const canRefund = ["PAID", "FULFILLMENT_PENDING", "SUPPLIER_PROCESSING", "FAILED", "COMPLETED"].includes(o.status);
  const canCancel = o.status === "PENDING_PAYMENT";
  const m = modal(o.order_number, `<div class="stack">
    <div class="row wrap">${statusPill(o.status)} ${o.payment_status ? `<span class="pill">${esc(t("payment"))}: ${esc(o.payment_status)}</span>` : ""} ${o.fulfillment_status ? `<span class="pill">${esc(t("fulfillment"))}: ${esc(o.fulfillment_status)}</span>` : ""}</div>
    <div class="kv small"><span>${esc(t("user"))}</span><span>${esc(o.user || "")} (#${o.user_id})</span>
      <span>${esc(t("total"))}</span><span>${money(o.total)}</span><span>${esc(t("f_supplier_cost"))}</span><span>${money(o.supplier_cost)}</span>
      <span>${esc(t("fee"))}</span><span>${money(o.fee)}</span><span>${esc(t("profit"))}</span><span>${money(o.profit)}</span>
      <span>${esc(t("created"))}</span><span>${dateTime(o.created_at)}</span><span>Idempotency</span><span class="mono tiny">${esc(o.idempotency_key || "")}</span></div>
    <div class="inset" style="padding:10px 14px">${o.items.map((i) => `<div class="sum-row"><span>${esc(i.game_name || "")} — ${esc(i.variant_name)}<div class="tiny muted">${esc(Object.entries(i.player_info || {}).map(([k, v]) => k + ": " + v).join(" · "))}</div></span><b>${money(i.line_total)}</b></div>`).join("")}</div>
    <h4 style="margin:6px 0 0">${esc(t("a_payments"))}</h4>
    ${(o.payments || []).map((p) => `<div class="row small"><span class="grow">${esc(p.provider)} · ${esc(p.provider_payment_id || "")}</span>${statusPill(p.status)}<b>${money(p.amount)}</b></div>`).join("") || `<p class="muted small">—</p>`}
    <h4 style="margin:6px 0 0">${esc(t("supplier_tx"))}</h4>
    ${(o.supplier_transactions || []).map((x) => `<div class="small inset" style="padding:8px 12px"><b>${esc(x.fulfillment_status)}</b> · ${esc(x.supplier_order_id || "—")} · ${esc(t("attempts"))}: ${x.attempts}${x.error_message ? `<div class="tiny" style="color:var(--err)">${esc(x.error_code || "")} ${esc(x.error_message)}</div>` : ""}</div>`).join("") || `<p class="muted small">—</p>`}
    <div class="row wrap" style="justify-content:flex-end">
      ${canCancel ? `<button class="btn sm danger" data-act="cancel">${esc(t("cancel_order"))}</button>` : ""}
      ${canRetry ? `<button class="btn sm" data-act="retry">${icon("refresh", "sm")} ${esc(t("retry_fulfillment"))}</button>` : ""}
      ${canRefund ? `<button class="btn sm" data-act="refund">${icon("wallet", "sm")} ${esc(t("refund_to_balance"))}</button>` : ""}
    </div></div>`, { wide: true });
  m.el.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", async () => {
    const act = b.dataset.act;
    let body = {};
    if (act === "refund") {
      const reason = window.prompt(t("refund_reason"), "");
      if (reason === null) return;
      body = { reason };
    } else if (!(await confirmDialog(t("confirm_q"), { danger: act === "cancel" }))) return;
    const url = { cancel: "cancel", retry: "retry-fulfillment", refund: "refund-balance" }[act];
    try { await api(`/api/admin/orders/${o.id}/${url}`, { method: "POST", body }); toast(t("saved")); m.close(); reload(); }
    catch (e) { toast(errMsg(e), "err"); }
  }));
}

/* ---------- topups ---------- */
SECTIONS.topups = async (main, params, page = 1, status = "", search = "") => {
  const d = await api(`/api/admin/topups?page=${page}&page_size=25${status ? "&status=" + status : ""}${search ? "&search=" + encodeURIComponent(search) : ""}`);
  const st = d.stats || {};
  main.innerHTML = `${head(t("a_topups"), `<input class="input" style="min-height:36px;width:170px" placeholder="${esc(t("search"))}" value="${esc(search)}" data-s>
      <select class="input" style="min-height:36px;width:auto" data-f>${["", "PENDING", "PAID", "CANCELLED", "FAILED"].map((s) => `<option value="${s}" ${s === status ? "selected" : ""}>${esc(s ? t("st_" + s) : t("all"))}</option>`).join("")}</select>`)}
    ${Object.keys(st).length ? `<div class="grid g4">${Object.entries(st).slice(0, 4).map(([k, v]) => `<div class="card stat"><div class="l">${esc(t("s_" + k))}</div><div class="v">${/pending/.test(k) ? esc(v) : money(v)}</div></div>`).join("")}</div>` : ""}
    ${table(["Ref", t("user"), t("amount"), t("credited"), t("status"), t("created"), ""], d.items, (x) => `<tr>
      <td class="mono small">${esc(x.reference)}<div class="tiny muted">${esc(x.provider_payment_id || "")}</div></td><td>${esc(x.username || "#" + x.user_id)}</td>
      <td>${som(x.pay_amount)}</td><td>${x.credited_amount ? money(x.credited_amount) : "—"}</td><td>${statusPill(x.status)}</td><td class="small">${dateTime(x.created_at)}</td>
      <td>${x.status === "PENDING" || x.status === "CANCELLED" ? `<button class="btn sm" data-re="${x.id}" title="${esc(t("recheck"))}">${icon("refresh", "sm")}</button>` : ""}</td></tr>`)}
    ${pager(d, (p) => SECTIONS.topups(main, params, p, status, search))}`;
  main.querySelector("[data-f]").addEventListener("change", (e) => SECTIONS.topups(main, params, 1, e.target.value, search));
  main.querySelector("[data-s]").addEventListener("change", (e) => SECTIONS.topups(main, params, 1, status, e.target.value.trim()));
  main.querySelectorAll("[data-re]").forEach((b) => b.addEventListener("click", () => withBusy(b, async () => {
    try { const r = await api(`/api/admin/topups/${b.dataset.re}/recheck`, { method: "POST" }); toast(t("st_" + r.topup.status), "info"); SECTIONS.topups(main, params, page, status, search); }
    catch (e) { toast(errMsg(e), "err"); }
  })));
};

SECTIONS.wallet = async (main, params, page = 1) => {
  const uid = params.user || "";
  const d = await api(`/api/admin/wallet/transactions?page=${page}&page_size=30${uid ? "&user_id=" + uid : ""}`);
  main.innerHTML = `${head(t("a_wallet_tx") + (uid ? ` · #${uid}` : ""))}
    ${table(["#", t("user"), t("f_type"), t("amount"), t("balance_after"), "Ref", t("note"), t("created")], d.items, (x) => `<tr>
      <td>${x.id}</td><td>#${x.user_id}</td><td>${esc(t("tx_" + x.kind))}</td><td style="color:${x.amount >= 0 ? "var(--ok)" : "var(--err)"}"><b>${x.amount >= 0 ? "+" : "−"}${money(Math.abs(x.amount))}</b></td>
      <td>${money(x.balance_after)}</td><td class="mono tiny">${esc(x.reference || "")}</td><td class="small">${esc(x.note || "")}</td><td class="small">${dateTime(x.created_at)}</td></tr>`)}
    ${pager(d, (p) => SECTIONS.wallet(main, params, p))}`;
};

SECTIONS.payments = async (main, params, page = 1) => {
  const d = await api(`/api/admin/payments?page=${page}&page_size=30`);
  main.innerHTML = `${head(t("a_payments"))}
    ${table(["#", t("order"), "Provider", "Provider ID", t("amount"), t("status"), t("created")], d.items, (p) => `<tr>
      <td>${p.id}</td><td class="mono small">${esc(p.order_number || "")}</td><td>${esc(p.provider)}</td><td class="mono tiny">${esc(p.provider_payment_id || "")}</td>
      <td>${money(p.amount)}</td><td>${statusPill(p.status)}</td><td class="small">${dateTime(p.created_at)}</td></tr>`)}
    ${pager(d, (p) => SECTIONS.payments(main, params, p))}`;
};

SECTIONS.fulfillments = async (main, params, page = 1) => {
  const d = await api(`/api/admin/fulfillments?page=${page}&page_size=30`);
  main.innerHTML = `${head(t("a_fulfillments"))}
    ${table(["#", t("order"), t("status"), t("attempts"), t("last_error"), t("next_attempt")], d.items, (f) => `<tr>
      <td>${f.id}</td><td class="mono small">${esc(f.order_number || "")}</td><td>${statusPill(f.status)}</td><td>${f.attempts}/${f.max_attempts}</td>
      <td class="small" style="color:var(--err)">${esc(f.last_error || "")}</td><td class="small">${f.next_attempt_at ? dateTime(f.next_attempt_at) : "—"}</td></tr>`)}
    ${pager(d, (p) => SECTIONS.fulfillments(main, params, p))}`;
};

SECTIONS.market = async (main, params, page = 1, status = "PENDING_APPROVAL") => {
  const d = await api(`/api/admin/market?page=${page}&page_size=25${status ? "&status=" + status : ""}`);
  main.innerHTML = `${head(t("a_market"), `<select class="input" style="min-height:36px;width:auto" data-f>${["", "PENDING_APPROVAL", "ACTIVE", "SOLD", "REJECTED"].map((s) => `<option value="${s}" ${s === status ? "selected" : ""}>${esc(s ? t("st_" + s) : t("all"))}</option>`).join("")}</select>`)}
    ${table(["#", t("title"), t("game"), t("seller"), t("price"), t("status"), ""], d.items, (l) => `<tr>
      <td>${l.id}</td><td><b>${esc(l.title)}</b><div class="tiny muted" style="max-width:320px;white-space:pre-line">${esc((l.description || "").slice(0, 200))}</div></td><td>${esc(l.game ? l.game.name : "")}</td>
      <td>${esc(l.seller)}${l.seller_telegram_id ? `<div class="tiny muted">TG ${esc(l.seller_telegram_id)}</div>` : ""}</td><td>${money(l.price)}</td><td>${statusPill(l.status)}</td>
      <td class="nowrap">${l.status === "PENDING_APPROVAL" ? `<button class="btn sm" data-mod="approve" data-id="${l.id}">${icon("check", "sm")}</button><button class="btn sm danger" data-mod="reject" data-id="${l.id}">${icon("close", "sm")}</button>` : ""}
      ${l.status === "ACTIVE" ? `<button class="btn sm" data-mod="sold" data-id="${l.id}">${esc(t("st_SOLD"))}</button>` : ""}</td></tr>`)}
    ${pager(d, (p) => SECTIONS.market(main, params, p, status))}`;
  main.querySelector("[data-f]").addEventListener("change", (e) => SECTIONS.market(main, params, 1, e.target.value));
  main.querySelectorAll("[data-mod]").forEach((b) => b.addEventListener("click", async () => {
    const note = b.dataset.mod === "reject" ? window.prompt(t("reject_reason"), "") : "";
    if (note === null) return;
    try { await api(`/api/admin/market/${b.dataset.id}/moderate`, { method: "POST", body: { action: b.dataset.mod, note } }); SECTIONS.market(main, params, page, status); }
    catch (e) { toast(errMsg(e), "err"); }
  }));
};

/* ---------- users ---------- */
SECTIONS.users = async (main, params, page = 1, search = "") => {
  const d = await api(`/api/admin/users?page=${page}&page_size=25${search ? "&search=" + encodeURIComponent(search) : ""}`);
  main.innerHTML = `${head(t("a_users"), `<input class="input" style="min-height:36px;width:220px" placeholder="${esc(t("user_search_ph"))}" value="${esc(search)}" data-s>`)}
    ${table(["#", t("username"), "Telegram", t("role"), t("your_balance"), t("f_active"), ""], d.items, (u) => `<tr>
      <td>${u.id}</td><td><b>${esc(u.username)}</b><div class="tiny muted">${esc(u.email || "")}</div></td><td class="mono small">${esc(u.telegram_id || "—")}</td>
      <td><span class="pill ${u.role === "ADMIN" ? "info" : ""}">${esc(u.role)}</span></td><td><b>${money(u.balance)}</b></td><td>${yes(u.is_active)}</td>
      <td class="nowrap"><button class="btn sm" data-bal="${u.id}" title="${esc(t("adjust_balance"))}">${icon("wallet", "sm")}</button>
      <a class="btn sm" href="#/admin/wallet?user=${u.id}" title="${esc(t("a_wallet_tx"))}">${icon("exchange", "sm")}</a>
      <button class="btn sm" data-edit="${u.id}">${icon("edit", "sm")}</button></td></tr>`)}
    ${pager(d, (p) => SECTIONS.users(main, params, p, search))}`;
  main.querySelector("[data-s]").addEventListener("change", (e) => SECTIONS.users(main, params, 1, e.target.value.trim()));
  const reload = () => SECTIONS.users(main, params, page, search);
  main.querySelectorAll("[data-bal]").forEach((b) => b.addEventListener("click", () => {
    const u = d.items.find((x) => String(x.id) === b.dataset.bal);
    formModal(`${t("adjust_balance")} — ${u.username}`, [
      { name: "amount", label: t("amount_signed"), type: "number", required: true, help: `${t("your_balance")}: ${money(u.balance)}` },
      { name: "note", label: t("note"), required: true, max: 200 },
    ], {}, async (data) => { await api(`/api/admin/users/${u.id}/balance`, { method: "POST", body: data }); reload(); }, { wide: false });
  }));
  main.querySelectorAll("[data-edit]").forEach((b) => b.addEventListener("click", () => {
    const u = d.items.find((x) => String(x.id) === b.dataset.edit);
    formModal(u.username, [
      { name: "role", label: t("role"), type: "select", options: [["CUSTOMER", "CUSTOMER"], ["SUPPORT", "SUPPORT"], ["ADMIN", "ADMIN"]] },
      { name: "is_active", label: t("f_active"), type: "checkbox" },
    ], u, async (data) => { await api(`/api/admin/users/${u.id}`, { method: "PATCH", body: data }); reload(); }, { wide: false });
  }));
};

SECTIONS.broadcast = async (main) => {
  const d = await api("/api/admin/notifications?page_size=15");
  main.innerHTML = `${head(t("a_broadcast"))}
    <form class="card stack"><label class="field"><span>${esc(t("title"))}</span><input class="input" name="title" required maxlength="120"></label>
      <label class="field"><span>${esc(t("message"))}</span><textarea class="input" name="text" required maxlength="2000"></textarea></label>
      <p class="help">${esc(t("broadcast_help"))}</p><button class="btn primary">${icon("send", "sm")} ${esc(t("send"))}</button></form>
    <div class="card"><h3>${esc(t("recent"))}</h3><div class="list">${d.items.map((n) => `<div class="li small"><span class="grow">#${n.user_id} · ${esc(n.kind)} · ${esc(n.title_key)}</span><span class="muted">${dateTime(n.created_at)}</span></div>`).join("") || `<p class="muted">—</p>`}</div></div>`;
  const form = main.querySelector("form");
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!(await confirmDialog(t("broadcast_confirm")))) return;
    withBusy(form.querySelector("button"), async () => {
      try { const r = await api("/api/admin/notifications/broadcast", { method: "POST", body: { title: form.title.value.trim(), text: form.text.value.trim() } }); toast(`${t("sent")}: ${r.sent}`); form.reset(); }
      catch (err) { toast(errMsg(err), "err"); }
    });
  });
};

/* ---------- integrations (.env managed from the panel) ---------- */
const GROUPS = [
  ["payerpin", "Payerpin", "box", ["PAYERPIN_API_KEY", "PAYERPIN_BASE_URL", "PAYERPIN_WEBHOOK_SECRET"]],
  ["hamyon", "Hamyon API (HUMO / UZCARD)", "card", ["HAMYON_SHOP_ID", "HAMYON_SHOP_KEY", "HAMYON_BASE_URL"]],
  ["telegram", "Telegram", "telegram", ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ADMIN_IDS"]],
  ["general", "General", "globe", ["PUBLIC_BASE_URL", "WEBAPP_URL", "CATALOG_SYNC_INTERVAL_MINUTES"]],
  ["payment", "Direct payment provider (optional)", "shield", ["PAYMENT_PROVIDER", "PAYMENT_API_KEY", "PAYMENT_SECRET", "PAYMENT_WEBHOOK_SECRET"]],
];
SECTIONS.integrations = async (main) => {
  const d = await api("/api/admin/integrations");
  const byKey = Object.fromEntries(d.items.map((x) => [x.key, x]));
  const cb = d.callbacks;
  const st = d.status;
  main.innerHTML = `${head(t("a_integrations"))}
    ${notice(esc(t("integrations_note")), "info", "lock")}
    ${!st.public_base_url_https ? notice(esc(t("need_https")), "", "alert") : ""}
    ${GROUPS.map(([g, title, ic, keys]) => `<form class="card stack" data-group="${g}">
      <div class="row between"><h3 style="margin:0">${icon(ic)} ${esc(title)}</h3>
        <div class="row">${g === "telegram" ? `<span class="pill ${st.telegram_bot_running ? "ok" : ""}">${esc(t(st.telegram_bot_running ? "bot_running" : "bot_stopped"))}</span>` : ""}
        ${["payerpin", "hamyon", "telegram"].includes(g) ? cfgPill(st[g]) : ""}</div></div>
      <div class="grid g2">${keys.map((k) => {
        const it = byKey[k] || { key: k };
        return `<label class="field"><span>${esc(k)}${it.restart_required ? ` <span class="pill">${esc(t("restart_needed"))}</span>` : ""}</span>
          <input class="input mono" name="${k}" ${it.secret ? `type="password" autocomplete="new-password" placeholder="${esc(it.configured ? "•••••••• (" + t("configured") + ")" : t("not_configured"))}"` : `value="${esc(it.value || "")}"`}>
          ${it.secret && it.configured ? `<label class="check tiny" style="margin-top:6px"><input type="checkbox" name="${k}__clear"> ${esc(t("clear_value"))}</label>` : ""}</label>`;
      }).join("")}</div>
      ${g === "hamyon" ? `<div class="inset small" style="padding:10px 14px"><b>${esc(t("hamyon_callbacks"))}</b>
        <div class="row"><span class="grow mono tiny ellipsis">prepare_url: ${esc(cb.hamyon_prepare_url || t("set_public_url"))}</span>${cb.hamyon_prepare_url ? `<button type="button" class="btn sm" data-copy="${esc(cb.hamyon_prepare_url)}">${icon("copy", "sm")}</button>` : ""}</div>
        <div class="row"><span class="grow mono tiny ellipsis">complete_url: ${esc(cb.hamyon_complete_url || t("set_public_url"))}</span>${cb.hamyon_complete_url ? `<button type="button" class="btn sm" data-copy="${esc(cb.hamyon_complete_url)}">${icon("copy", "sm")}</button>` : ""}</div>
        <p class="help">${esc(t("hamyon_help"))}</p></div>` : ""}
      ${g === "payerpin" ? `<div class="inset small" style="padding:10px 14px"><div class="row"><span class="grow mono tiny ellipsis">Webhook: ${esc(cb.payerpin_webhook_url || t("set_public_url"))}</span>${cb.payerpin_webhook_url ? `<button type="button" class="btn sm" data-copy="${esc(cb.payerpin_webhook_url)}">${icon("copy", "sm")}</button>` : ""}</div></div>` : ""}
      ${g === "telegram" ? `<p class="help">${esc(t("telegram_help"))}</p>` : ""}
      <div class="row" style="justify-content:flex-end">
        ${["payerpin", "hamyon", "telegram"].includes(g) ? `<button type="button" class="btn sm" data-test="${g}">${icon("pulse", "sm")} ${esc(t("test_connection"))}</button>` : ""}
        <button class="btn sm primary">${icon("check", "sm")} ${esc(t("save"))}</button></div>
      <div class="small" data-result></div>
    </form>`).join("")}`;
  main.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => copyText(b.dataset.copy)));
  main.querySelectorAll("form[data-group]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const values = {};
      for (const el of form.elements) {
        if (!el.name) continue;
        if (el.type === "checkbox") { if (el.checked) values[el.name] = ""; continue; }
        values[el.name] = el.value;
      }
      withBusy(form.querySelector(".btn.primary"), async () => {
        try {
          const r = await api("/api/admin/integrations", { method: "PUT", body: { values } });
          toast(r.changed.length ? `${t("saved")}: ${r.changed.join(", ")}` : t("no_changes"), r.changed.length ? "ok" : "info");
          const cfg = await api("/api/config").catch(() => null);
          if (cfg) state.config = cfg;
          SECTIONS.integrations(main);
        } catch (err) { toast(errMsg(err), "err"); }
      });
    });
  });
  main.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", () => withBusy(b, async () => {
    const out = b.closest("form").querySelector("[data-result]");
    try {
      const r = await api(`/api/admin/integrations/test/${b.dataset.test}`, { method: "POST" });
      out.innerHTML = notice(`<b>${esc(r.message || "")}</b>${r.bot_username ? " · @" + esc(r.bot_username) : ""}`, r.ok ? "info" : "err", r.ok ? "checkCircle" : "x");
    } catch (e) { out.innerHTML = notice(esc(errMsg(e)), "err", "x"); }
  })));
};

/* ---------- payerpin supplier page ---------- */
SECTIONS.payerpin = async (main) => {
  const d = await api("/api/admin/suppliers/payerpin");
  main.innerHTML = `${head("Payerpin", cfgPill(d.api_configured))}
    ${!d.api_configured ? notice(`${esc(t("payerpin_nc"))} <a href="#/admin/integrations">${esc(t("a_integrations"))}</a>`, "", "plug") : ""}
    ${d.low_balance_warning ? notice(esc(t("low_balance")), "err", "alert") : ""}
    <div class="grid g4">
      <div class="card stat"><div class="l">${esc(t("supplier_balance"))}</div><div class="v">${d.balance === null || d.balance === undefined ? "—" : esc(Number(d.balance).toLocaleString("ru-RU")) + " " + esc(d.balance_currency || "")}</div><div class="tiny muted">${d.balance_checked_at ? dateTime(d.balance_checked_at) : ""}</div></div>
      <div class="card stat"><div class="l">${esc(t("active_supplier_products"))}</div><div class="v">${d.active_supplier_products}</div></div>
      <div class="card stat"><div class="l">${esc(t("s_fulfillments_pending"))}</div><div class="v">${d.pending_fulfillments}</div></div>
      <div class="card stat"><div class="l">${esc(t("failed_fulfillments"))}</div><div class="v">${d.failed_fulfillments}</div></div>
    </div>
    <div class="card stack">
      <div class="kv small"><span>Base URL</span><span class="mono">${esc(d.base_url)}</span><span>API key</span><span>${d.api_key_configured ? esc(t("configured")) : esc(t("not_configured"))}</span>
        <span>${esc(t("last_success"))}</span><span>${d.last_success_request ? dateTime(d.last_success_request) : "—"}</span>
        <span>${esc(t("last_failed"))}</span><span>${d.last_failed_request ? dateTime(d.last_failed_request) : "—"}</span>
        <span>${esc(t("completed_supplier_orders"))}</span><span>${d.completed_supplier_orders}</span></div>
      <div class="row wrap">
        <button class="btn" data-a="test-connection" ${d.api_configured ? "" : "disabled"}>${icon("pulse", "sm")} ${esc(t("test_connection"))}</button>
        <button class="btn" data-a="check-balance" ${d.api_configured ? "" : "disabled"}>${icon("wallet", "sm")} ${esc(t("check_balance"))}</button>
        <button class="btn primary" data-a="sync-catalog" ${d.api_configured ? "" : "disabled"}>${icon("refresh", "sm")} ${esc(t("sync_catalog"))}</button>
      </div>
      <p class="help">${esc(t("payerpin_safe"))}</p>
      <div data-out></div>
    </div>
    <h3>${esc(t("sync_history"))}</h3>
    ${table([t("status"), t("a_games"), t("a_products"), t("a_variants"), t("activated"), t("deactivated"), t("created")], d.sync_history, (s) => `<tr>
      <td>${statusPill(s.status)}${s.error ? `<div class="tiny" style="color:var(--err)">${esc(s.error)}</div>` : ""}</td><td>${s.games}</td><td>${s.products}</td><td>${s.variants}</td><td>${s.activated}</td><td>${s.deactivated}</td><td class="small">${dateTime(s.started_at)}</td></tr>`)}`;
  main.querySelectorAll("[data-a]").forEach((b) => b.addEventListener("click", () => withBusy(b, async () => {
    const out = main.querySelector("[data-out]");
    try {
      const r = await api(`/api/admin/suppliers/payerpin/${b.dataset.a}`, { method: "POST" });
      let msg = r.message || t("done");
      if (b.dataset.a === "check-balance") msg = `${t("supplier_balance")}: ${r.balance} ${r.currency || ""}`;
      if (b.dataset.a === "sync-catalog") {
        msg = `${t("sync_done")}: ${r.games_found ?? r.games ?? 0} / ${r.products_found ?? r.products ?? 0} / ${r.variants_found ?? r.variants ?? 0}`;
        if (r.currencies_missing_rate && r.currencies_missing_rate.length) msg += ` — ${t("missing_rates")}: ${r.currencies_missing_rate.join(", ")}`;
      }
      out.innerHTML = notice(esc(msg), "info", "checkCircle");
      setTimeout(() => SECTIONS.payerpin(main), 1200);
    } catch (e) { out.innerHTML = notice(esc(errMsg(e)), "err", "x"); }
  })));
};

/* ---------- branding ---------- */
SECTIONS.branding = async (main) => {
  const d = (await api("/api/admin/branding")).branding;
  const L = (base, label, type = "text") => ["uz", "en", "ru"].map((l) => ({ name: `${base}_${l}`, label: `${label} (${l.toUpperCase()})`, type, max: 600 }));
  const fields = [
    { name: "site_name", label: t("site_name"), required: true, max: 40 },
    { name: "support_hours", label: t("working_hours"), max: 60 },
    { name: "logo_url", label: t("f_logo"), type: "image", kind: "logo" },
    { name: "favicon_url", label: "Favicon", type: "image", kind: "logo" },
    { name: "hero_image_url", label: t("hero_image"), type: "image", kind: "banner", help: t("hero_image_help") },
    { name: "og_image_url", label: t("og_image"), type: "image", kind: "banner" },
    ...L("hero_title", t("hero_title_f")),
    ...L("hero_subtitle", t("hero_subtitle_f"), "textarea"),
    ...L("announcement", t("announcement"), "textarea"),
    ...L("footer_text", t("footer_text"), "textarea"),
    { name: "support_telegram", label: "Support Telegram (@username)", max: 100 },
    { name: "telegram_channel_url", label: t("channel") + " URL", max: 200 },
    { name: "support_email", label: "Support email", max: 120 },
    { name: "support_phone", label: t("phone"), max: 40 },
    { name: "instagram_url", label: "Instagram URL", max: 200 },
  ].map((f) => ({ ...f, emptyAs: "" }));
  main.innerHTML = `${head(t("a_branding"))}<form class="card stack"><div class="grid g2">${fields.map((f) => fieldHtml(f, d[f.name])).join("")}</div>
    <button class="btn primary">${icon("check", "sm")} ${esc(t("save"))}</button></form>`;
  const form = main.querySelector("form");
  bindImageInputs(form);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    withBusy(form.querySelector(".btn.primary"), async () => {
      try {
        await api("/api/admin/branding", { method: "PUT", body: { value: readForm(form, fields) } });
        state.config = await api("/api/config");
        renderShell();
        toast(t("saved"));
      } catch (err) { toast(errMsg(err), "err"); }
    });
  });
};

/* ---------- media library ---------- */
SECTIONS.media = async (main) => {
  const d = await api("/api/admin/media");
  main.innerHTML = `${head(t("a_media"), `<button class="btn sm primary" data-up>${icon("upload", "sm")} ${esc(t("upload"))}</button>`)}
    <p class="help">${esc(t("media_help"))}</p>
    ${d.items.length ? `<div class="media-grid">${d.items.map((m) => `<div class="card m"><img src="${safeUrl(m.url)}" alt="" loading="lazy">
      <div class="tiny muted ellipsis" title="${esc(m.name)}">${esc(m.name)}</div><div class="row" style="gap:6px;margin-top:6px">
      <button class="btn sm grow" data-copy="${esc(m.url)}">${icon("copy", "sm")}</button><button class="btn sm danger" data-del="${esc(m.name)}">${icon("trash", "sm")}</button></div></div>`).join("")}</div>`
      : `<div class="card">${empty("image", t("no_media"))}</div>`}`;
  main.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", () => copyText(b.dataset.copy)));
  main.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
    if (!(await confirmDialog(t("delete_q"), { danger: true }))) return;
    try { await api(`/api/admin/media/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); SECTIONS.media(main); } catch (e) { toast(errMsg(e), "err"); }
  }));
  main.querySelector("[data-up]").addEventListener("click", () => {
    const picker = document.createElement("input");
    picker.type = "file"; picker.accept = "image/png,image/jpeg,image/webp,image/gif,image/x-icon"; picker.multiple = true;
    picker.onchange = async () => {
      for (const file of picker.files) {
        const form = new FormData(); form.append("file", file); form.append("kind", "media");
        try { await api("/api/admin/uploads", { method: "POST", form }); } catch (e) { toast(`${file.name}: ${errMsg(e)}`, "err"); }
      }
      SECTIONS.media(main);
    };
    picker.click();
  });
};

/* ---------- top-up settings + FX rates ---------- */
SECTIONS["topup-settings"] = async (main) => {
  const d = await api("/api/admin/topup-settings");
  const tp = d.topup;
  const rates = d.rates || {};
  main.innerHTML = `${head(t("a_topup_settings"))}
    <form class="card stack" data-tp><h3>${esc(t("top_up_balance"))}</h3><div class="grid g2">
      ${fieldHtml({ name: "enabled", label: t("topup_enabled"), type: "checkbox" }, tp.enabled)}
      ${fieldHtml({ name: "auto_refund_failed_orders", label: t("auto_refund"), type: "checkbox" }, tp.auto_refund_failed_orders)}
      ${fieldHtml({ name: "min_amount", label: t("min_amount_sum"), type: "number" }, tp.min_amount)}
      ${fieldHtml({ name: "max_amount", label: t("max_amount_sum"), type: "number" }, tp.max_amount)}
      ${fieldHtml({ name: "presets", label: t("presets_sum"), full: true, help: "10000, 25000, 50000" }, (tp.presets || []).join(", "))}
    </div><button class="btn primary">${icon("check", "sm")} ${esc(t("save"))}</button></form>
    <form class="card stack" data-rates><h3>${esc(t("fx_rates"))}</h3><p class="help">${esc(t("fx_help"))}</p>
      <textarea class="input mono" name="rates" style="min-height:140px">${esc(Object.entries(rates).map(([k, v]) => `${k}=${v}`).join("\n"))}</textarea>
      <button class="btn primary">${icon("check", "sm")} ${esc(t("save"))}</button></form>`;
  const f1 = main.querySelector("[data-tp]");
  f1.addEventListener("submit", (e) => {
    e.preventDefault();
    const value = {
      enabled: f1.enabled.checked, auto_refund_failed_orders: f1.auto_refund_failed_orders.checked,
      min_amount: Number(f1.min_amount.value), max_amount: Number(f1.max_amount.value),
      presets: f1.presets.value.split(/[,\s]+/).filter(Boolean).map(Number),
    };
    withBusy(f1.querySelector("button"), async () => {
      try { await api("/api/admin/topup-settings", { method: "PUT", body: { value } }); toast(t("saved")); } catch (err) { toast(errMsg(err), "err"); }
    });
  });
  const f2 = main.querySelector("[data-rates]");
  f2.addEventListener("submit", (e) => {
    e.preventDefault();
    const value = {};
    f2.rates.value.split(/\n+/).forEach((line) => {
      const [k, v] = line.split("=").map((x) => (x || "").trim());
      if (k && v) value[k.toUpperCase()] = Number(v);
    });
    withBusy(f2.querySelector("button"), async () => {
      try { await api("/api/admin/rates", { method: "PUT", body: { value } }); toast(t("saved")); } catch (err) { toast(errMsg(err), "err"); }
    });
  });
};

SECTIONS.telegram = async (main) => {
  const d = await api("/api/admin/telegram");
  main.innerHTML = `${head("Telegram", cfgPill(d.configured))}
    <div class="card kv small"><span>${esc(t("bot_token"))}</span><span>${cfgPill(d.configured)}</span><span>${esc(t("admin_ids"))}</span><span>${d.admin_ids_configured}</span></div>
    <p class="help">${esc(t("telegram_help"))} <a href="#/admin/integrations">${esc(t("a_integrations"))}</a></p>
    ${table(["Telegram ID", t("username"), t("f_name"), t("language"), t("linked"), t("created")], d.users, (u) => `<tr>
      <td class="mono">${esc(u.telegram_id)}</td><td>${esc(u.username ? "@" + u.username : "—")}</td><td>${esc(u.first_name || "")}</td><td>${esc(u.language || "")}</td><td>${yes(u.linked)}</td><td class="small">${dateTime(u.created_at)}</td></tr>`)}`;
};

SECTIONS.audit = async (main, params, page = 1, search = "") => {
  const d = await api(`/api/admin/audit-logs?page=${page}&page_size=30${search ? "&search=" + encodeURIComponent(search) : ""}`);
  main.innerHTML = `${head(t("a_audit"), `<input class="input" style="min-height:36px;width:200px" placeholder="${esc(t("search"))}" value="${esc(search)}" data-s>`)}
    ${table(["#", t("action"), t("target"), t("result"), "Admin", t("created")], d.items, (a) => `<tr>
      <td>${a.id}</td><td><b>${esc(a.action)}</b>${a.details ? `<div class="tiny muted mono" style="max-width:340px;overflow:hidden;text-overflow:ellipsis">${esc(JSON.stringify(a.details))}</div>` : ""}</td>
      <td class="small">${esc(a.target_type ? a.target_type + ":" + (a.target_id ?? "") : "")}</td><td>${statusPill(a.result)}</td><td>#${esc(a.admin_user_id ?? "")}</td><td class="small">${dateTime(a.created_at)}</td></tr>`)}
    ${pager(d, (p) => SECTIONS.audit(main, params, p, search))}`;
  main.querySelector("[data-s]").addEventListener("change", (e) => SECTIONS.audit(main, params, 1, e.target.value.trim()));
};

SECTIONS.security = async (main) => {
  const d = await api("/api/admin/security");
  main.innerHTML = `${head(t("a_security"))}
    <div class="card kv small"><span>Password hashing</span><span>${esc(d.password_hashing)}</span><span>Sessions</span><span>${esc(d.session_storage)}</span>
      <span>Rate limiting</span><span>${yes(d.rate_limiting)}</span><span>Webhook signatures</span><span>${esc(d.webhook_signatures)}</span>
      <span>${esc(t("locked_accounts"))}</span><span>${d.locked_accounts}</span><span>${esc(t("failed_webhooks"))}</span><span>${d.failed_webhooks}</span></div>
    <div class="card"><h3>${esc(t("recent"))}</h3><div class="list">${d.recent_admin_actions.map((a) => `<div class="li small"><b class="grow">${esc(a.action)}</b><span class="muted">${esc(a.target || "")}</span>${statusPill(a.result)}<span class="muted">${dateTime(a.created_at)}</span></div>`).join("") || "—"}</div></div>`;
};
