/* VYRON core: state, API client, formatting, toasts, modals, router. */
import { icon } from "./icons.js";
import { t, getLang, setLang, errorText } from "./i18n.js";

export { icon, t, getLang, setLang };

export const tg = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData
  ? window.Telegram.WebApp
  : null;

export const state = {
  user: null,
  config: null,
  cartCount: 0,
  unread: 0,
};

/* ---------------- HTML helpers ---------------- */
export function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
/** Only allow http(s) or site-relative URLs in src/href attributes. */
export function safeUrl(url, fallback = "") {
  const u = String(url || "").trim();
  if (/^(https?:\/\/|\/(?!\/))/i.test(u)) return esc(u);
  return esc(fallback);
}
export const FALLBACK_IMG = "/api/art/currency.svg?c=";

/* ---------------- formatting ---------------- */
/** Money is stored in minor units (tiyin). */
export function money(minor, currency = "UZS") {
  if (minor === null || minor === undefined || minor === "") return t("not_available");
  const value = Math.round(Number(minor) / 100);
  const s = value.toLocaleString("ru-RU").replace(/\u00a0/g, " ");
  return currency === "UZS" ? `${s} ${t("sum")}` : `${s} ${currency}`;
}
export function som(value) {
  return `${Number(value || 0).toLocaleString("ru-RU").replace(/\u00a0/g, " ")} ${t("sum")}`;
}
export function dateTime(iso) {
  if (!iso) return "—";
  const d = new Date(/Z$|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
  if (isNaN(d)) return "—";
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}.${p(d.getMonth() + 1)}.${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

const STATUS_KIND = {
  COMPLETED: "ok", PAID: "info", FULFILLMENT_PENDING: "info", SUPPLIER_PROCESSING: "info",
  PENDING_PAYMENT: "warn", PENDING: "warn", FAILED: "err", REFUNDED: "", CANCELLED: "",
  ACTIVE: "ok", PENDING_APPROVAL: "warn", REJECTED: "err", SOLD: "", EXPIRED: "",
  SUCCEEDED: "ok", PROCESSING: "info", AWAITING_STATUS: "info", SUCCESS: "ok",
};
export function statusPill(status) {
  if (!status) return "";
  return `<span class="pill ${STATUS_KIND[status] ?? ""}">${esc(t("st_" + status))}</span>`;
}

/* ---------------- API ---------------- */
const TOKEN_KEY = "vyron_token";
export function setToken(token) {
  try { token ? sessionStorage.setItem(TOKEN_KEY, token) : sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* ignore */ }
}
function getToken() {
  try { return sessionStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
}

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : (detail && detail.code) || "error");
    this.status = status;
    this.detail = detail;
  }
}

export async function api(path, { method = "GET", body, form } = {}) {
  const headers = { Accept: "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
  } catch (e) {
    throw new ApiError(0, "network_error");
  }
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
  if (!res.ok) {
    let detail = data && data.detail !== undefined ? data.detail : `http_${res.status}`;
    if (Array.isArray(detail)) detail = "validation_error";
    throw new ApiError(res.status, detail);
  }
  return data;
}
export function errMsg(err) {
  if (err instanceof ApiError) return errorText(err.detail);
  return errorText("error");
}

/* ---------------- toasts ---------------- */
export function toast(message, kind = "ok") {
  const root = document.getElementById("toasts");
  const node = document.createElement("div");
  node.className = `toast ${kind}`;
  node.innerHTML = `${icon(kind === "err" ? "x" : kind === "info" ? "info" : "checkCircle")}<span>${esc(message)}</span>`;
  root.appendChild(node);
  if (tg && tg.HapticFeedback) {
    try { tg.HapticFeedback.notificationOccurred(kind === "err" ? "error" : "success"); } catch (e) { /* */ }
  }
  setTimeout(() => node.remove(), 3600);
}

/* ---------------- modal ---------------- */
export function modal(title, bodyHtml, { wide = false, onMount } = {}) {
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal ${wide ? "wide" : ""}" role="dialog" aria-modal="true">
      <div class="modal-head"><h3>${esc(title)}</h3>
      <button class="icon-btn" data-close aria-label="${esc(t("close"))}">${icon("close")}</button></div>
      <div class="modal-body">${bodyHtml}</div></div>`;
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  back.addEventListener("click", (e) => {
    if (e.target === back || e.target.closest("[data-close]")) close();
  });
  document.addEventListener("keydown", onKey);
  document.getElementById("modals").appendChild(back);
  const el = back.querySelector(".modal-body");
  if (onMount) onMount(el, close);
  return { el, close };
}
export function confirmDialog(message, { danger = false, okText } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const m = modal(t("confirm"), `<p>${esc(message)}</p>
      <div class="row" style="justify-content:flex-end;margin-top:16px">
        <button class="btn" data-no>${esc(t("cancel"))}</button>
        <button class="btn ${danger ? "danger" : "primary"}" data-yes>${esc(okText || t("confirm"))}</button>
      </div>`);
    m.el.querySelector("[data-no]").onclick = () => { done = true; m.close(); resolve(false); };
    m.el.querySelector("[data-yes]").onclick = () => { done = true; m.close(); resolve(true); };
    const obs = new MutationObserver(() => { if (!document.body.contains(m.el) && !done) { obs.disconnect(); resolve(false); } });
    obs.observe(document.getElementById("modals"), { childList: true });
  });
}

/* ---------------- misc UI ---------------- */
export function loading() {
  return `<div class="loading-page"><div class="spinner"></div></div>`;
}
export function empty(iconName, title, text = "", action = "") {
  return `<div class="empty"><div class="ic">${icon(iconName, "lg")}</div>
    <h3>${esc(title)}</h3>${text ? `<p>${esc(text)}</p>` : ""}${action}</div>`;
}
export function notice(text, kind = "", iconName = "alert") {
  return `<div class="notice ${kind}">${icon(iconName)}<div>${text}</div></div>`;
}
export function pageHead(title, { back = false, extra = "" } = {}) {
  return `<div class="page-head">${back ? `<button class="icon-btn back-btn" data-back aria-label="${esc(t("back"))}">${icon("back")}</button>` : ""}
    <h1 class="grow" style="font-size:clamp(22px,3.4vw,30px)">${esc(title)}</h1>${extra}</div>`;
}
/** Read a form into a plain object (checkbox -> bool, data-num -> number). */
export function formData(form) {
  const out = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    if (el.type === "checkbox") out[el.name] = el.checked;
    else if (el.type === "file") continue;
    else if (el.dataset.num !== undefined) out[el.name] = el.value === "" ? null : Number(el.value);
    else out[el.name] = el.value;
  }
  return out;
}
export async function withBusy(button, fn) {
  if (!button) return fn();
  const html = button.innerHTML;
  button.disabled = true;
  button.innerHTML = `<span class="spinner" style="width:18px;height:18px;border-width:2px"></span>`;
  try { return await fn(); } finally { button.disabled = false; button.innerHTML = html; }
}
export function copyText(text) {
  const done = () => toast(t("copied"));
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(done, () => fallbackCopy(text, done));
  } else fallbackCopy(text, done);
}
function fallbackCopy(text, done) {
  const ta = document.createElement("textarea");
  ta.value = text; document.body.appendChild(ta); ta.select();
  try { document.execCommand("copy"); done(); } catch (e) { /* */ }
  ta.remove();
}
export function idemKey() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "k" + Date.now().toString(36) + Math.random().toString(36).slice(2);
}
export function localized(obj, base) {
  const lang = getLang();
  return (obj && (obj[`${base}_${lang}`] || obj[`${base}_en`] || obj[`${base}_uz`])) || "";
}

/* ---------------- router ---------------- */
export function navigate(hash) {
  if (location.hash === hash) window.dispatchEvent(new HashChangeEvent("hashchange"));
  else location.hash = hash;
}
export function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/";
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const params = Object.fromEntries(new URLSearchParams(query));
  return { path: "/" + parts.join("/"), parts, params };
}
export function requireLogin() {
  if (state.user) return true;
  navigate(`#/login?next=${encodeURIComponent(location.hash || "#/")}`);
  return false;
}
