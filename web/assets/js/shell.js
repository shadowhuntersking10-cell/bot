/* App shell: header, bottom tab bar, theme & language, badges. */
import { api, state, esc, safeUrl, t, icon, tg, getLang, setLang, navigate } from "./core.js";

const THEME_KEY = "vyron_theme";

export function getTheme() {
  return document.documentElement.dataset.theme || "night";
}
export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = theme === "day" ? "#e9f1fb" : "#0b1a33";
  if (tg) {
    try {
      tg.setHeaderColor(theme === "day" ? "#e9f1fb" : "#0b1a33");
      tg.setBackgroundColor(theme === "day" ? "#e9f1fb" : "#0b1a33");
    } catch (e) { /* older clients */ }
  }
}
export function initTheme() {
  let theme = null;
  try { theme = localStorage.getItem(THEME_KEY); } catch (e) { /* */ }
  if (!theme && tg && tg.colorScheme) theme = tg.colorScheme === "light" ? "day" : "night";
  if (!theme && window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches) theme = "day";
  applyTheme(theme === "day" ? "day" : "night");
}
export async function setTheme(theme) {
  applyTheme(theme);
  try { localStorage.setItem(THEME_KEY, theme); } catch (e) { /* */ }
  if (state.user) api("/api/account/profile", { method: "PATCH", body: { theme } }).catch(() => {});
}
export async function changeLang(lang) {
  setLang(lang);
  document.documentElement.lang = lang;
  if (state.user) api("/api/account/profile", { method: "PATCH", body: { language: lang } }).catch(() => {});
  renderShell();
  navigate(location.hash || "#/");
}

const TABS = [
  ["#/", "home", "nav_home"],
  ["#/games", "games", "nav_games"],
  ["#/cart", "cart", "nav_cart"],
  ["#/wallet", "wallet", "nav_wallet"],
  ["#/account", "user", "nav_profile"],
];

export function renderShell() {
  const b = (state.config && state.config.branding) || {};
  const name = b.site_name || "VYRON";
  const logo = safeUrl(b.logo_url, "/assets/img/logo.svg");
  document.title = `${name} — ${t("tagline")}`;
  const fav = document.querySelector('link[rel="icon"]');
  if (fav && b.favicon_url) fav.href = b.favicon_url;
  const isAdmin = !!(state.user && state.user.is_admin);
  const theme = getTheme();
  const lang = getLang();
  const langSel = `<select class="input lang-select" data-lang-select aria-label="${esc(t("language"))}">
      ${["uz", "en", "ru"].map((l) => `<option value="${l}" ${l === lang ? "selected" : ""}>${l.toUpperCase()}</option>`).join("")}</select>`;
  const themeBtn = `<button class="icon-btn" data-theme-toggle aria-label="${esc(t("theme"))}">${icon(theme === "day" ? "moon" : "sun")}</button>`;

  document.getElementById("topbar").innerHTML = `<div class="container">
    <a class="brand" href="#/"><img src="${logo}" alt=""><span>${esc(name)}</span></a>
    <nav class="nav">
      <a href="#/" data-nav="/">${esc(t("nav_home"))}</a>
      <a href="#/games" data-nav="/games">${esc(t("nav_games"))}</a>
      <a href="#/market" data-nav="/market">${esc(t("nav_market"))}</a>
      <a href="#/support" data-nav="/support">${esc(t("nav_support"))}</a>
      ${isAdmin ? `<a href="#/admin" data-nav="/admin" title="${esc(t("nav_admin"))}">${icon("settings", "sm")}<span class="nav-admin-label"> ${esc(t("nav_admin"))}</span></a>` : ""}
    </nav>
    <form class="search desk" data-search>${icon("search")}<input class="input" name="q" type="search" placeholder="${esc(t("search_placeholder"))}"></form>
    <div class="top-actions">
      <a class="icon-btn" href="#/search" style="display:none" data-mobile-search aria-label="${esc(t("search"))}">${icon("search")}</a>
      ${langSel}${themeBtn}
      <a class="icon-btn desk" href="#/cart" aria-label="${esc(t("nav_cart"))}">${icon("cart")}<span class="badge-dot hidden" data-cart-badge></span></a>
      <a class="icon-btn" href="#/notifications" aria-label="${esc(t("notifications"))}">${icon("bell")}<span class="badge-dot hidden" data-unread-badge></span></a>
      ${state.user
        ? `<a class="btn sm desk" href="#/wallet">${icon("wallet", "sm")} <span data-balance></span></a>`
        : `<a class="btn sm primary desk" href="#/login">${esc(t("login"))}</a>`}
    </div></div>`;

  document.getElementById("tghead").innerHTML = `<a class="brand" href="#/"><img src="${logo}" alt=""><span>${esc(name)}</span></a>
    <div class="grow"></div>
    <a class="icon-btn" href="#/search" aria-label="${esc(t("search"))}">${icon("search")}</a>
    ${isAdmin ? `<a class="icon-btn" href="#/admin" aria-label="${esc(t("nav_admin"))}">${icon("settings")}</a>` : ""}
    <a class="icon-btn" href="#/notifications" aria-label="${esc(t("notifications"))}">${icon("bell")}<span class="badge-dot hidden" data-unread-badge></span></a>
    ${themeBtn}`;

  document.getElementById("tabbar").innerHTML = TABS.map(([href, ic, label]) =>
    `<a href="${href}" data-tab="${href.slice(1)}">${icon(ic)}<span>${esc(t(label))}</span>${href === "#/cart" ? `<span class="badge-dot hidden" data-cart-badge></span>` : ""}</a>`).join("");

  const foot = document.getElementById("footer");
  foot.innerHTML = `<div class="container cols">
      <div><div class="brand" style="margin-bottom:8px"><img src="${logo}" alt=""><span>${esc(name)}</span></div>
      <p>${esc((b[`footer_text_${lang}`]) || t("footer_tagline"))}</p></div>
      <div><b style="color:var(--text)">${esc(t("nav_games"))}</b><div class="stack" style="margin-top:8px">
        <a href="#/games">${esc(t("all_games"))}</a><a href="#/market">${esc(t("nav_market"))}</a><a href="#/wallet">${esc(t("top_up_balance"))}</a></div></div>
      <div><b style="color:var(--text)">${esc(t("nav_support"))}</b><div class="stack" style="margin-top:8px">
        <a href="#/support">${esc(t("faq"))}</a>${b.support_telegram ? `<a href="${safeUrl(tgLink(b.support_telegram))}" target="_blank" rel="noopener">Telegram</a>` : ""}
        <span>© ${new Date().getFullYear()} ${esc(name)}</span></div></div></div>`;

  // mobile search icon visibility (topbar is hidden inside Telegram anyway)
  const ms = document.querySelector("[data-mobile-search]");
  if (ms && window.matchMedia("(max-width: 860px)").matches) ms.style.display = "";
  updateBadges();
  highlightNav();
}

export function tgLink(value) {
  const v = String(value || "").trim();
  if (/^https?:\/\//.test(v)) return v;
  return `https://t.me/${v.replace(/^@/, "")}`;
}

export function highlightNav() {
  const path = (location.hash.replace(/^#/, "").split("?")[0]) || "/";
  const first = "/" + (path.split("/")[1] || "");
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === first));
  const tabFor = { "/": "/", "/game": "/games", "/games": "/games", "/cart": "/cart", "/wallet": "/wallet",
    "/account": "/account", "/orders": "/account", "/order": "/account", "/login": "/account", "/register": "/account",
    "/notifications": "/account", "/admin": "/account", "/market": "/account", "/support": "/account" };
  const active = tabFor[first] || "";
  document.querySelectorAll("[data-tab]").forEach((a) => a.classList.toggle("active", a.dataset.tab === active));
}

export function updateBadges() {
  document.querySelectorAll("[data-cart-badge]").forEach((el) => {
    el.textContent = state.cartCount;
    el.classList.toggle("hidden", !state.cartCount);
  });
  document.querySelectorAll("[data-unread-badge]").forEach((el) => {
    el.textContent = state.unread > 9 ? "9+" : state.unread;
    el.classList.toggle("hidden", !state.unread);
  });
  document.querySelectorAll("[data-balance]").forEach((el) => {
    const v = Math.round(((state.user && state.user.balance) || 0) / 100).toLocaleString("ru-RU").replace(/\u00a0/g, " ");
    el.textContent = `${v} ${t("sum")}`;
  });
}

export async function refreshBadges() {
  if (!state.user) { state.cartCount = 0; state.unread = 0; updateBadges(); return; }
  const [cart, notes, me] = await Promise.all([
    api("/api/cart").catch(() => null),
    api("/api/account/notifications").catch(() => null),
    api("/api/auth/me").catch(() => null),
  ]);
  if (cart) state.cartCount = cart.cart.count || 0;
  if (notes) state.unread = (notes.items || []).filter((n) => !n.is_read).length;
  if (me && me.user) state.user = me.user;
  updateBadges();
}

export function bindShellEvents(onRoute) {
  document.addEventListener("click", (e) => {
    const tt = e.target.closest("[data-theme-toggle]");
    if (tt) { setTheme(getTheme() === "day" ? "night" : "day"); renderShell(); return; }
    const back = e.target.closest("[data-back]");
    if (back) { if (history.length > 1) history.back(); else navigate("#/"); }
  });
  document.addEventListener("change", (e) => {
    if (e.target.matches("[data-lang-select]")) changeLang(e.target.value);
  });
  document.addEventListener("submit", (e) => {
    const f = e.target.closest("[data-search]");
    if (f) { e.preventDefault(); navigate(`#/search?q=${encodeURIComponent(f.q.value.trim())}`); }
  });
  window.addEventListener("hashchange", onRoute);
}
