/* VYRON SPA bootstrap + router. */
import { api, state, tg, parseHash, setToken, esc, t, notice, loading, getLang, setLang } from "./core.js";
import { initTheme, renderShell, bindShellEvents, highlightNav, refreshBadges, applyTheme } from "./shell.js";
import { renderHome, renderGames, renderGame, renderSearch } from "./pages/shop.js";
import { renderCart } from "./pages/cart.js";
import { renderWallet } from "./pages/wallet.js";
import { renderAuth, renderReset, renderAccount, renderOrders, renderOrder, renderNotifications } from "./pages/account.js";
import { renderMarket, renderSupport } from "./pages/misc.js";

const view = () => document.getElementById("view");
let routeSeq = 0;

async function route() {
  const seq = ++routeSeq;
  const { parts, params } = parseHash();
  const el = view();
  el.innerHTML = loading();
  window.scrollTo(0, 0);
  highlightNav();
  if (tg) {
    try {
      if (parts.length) { tg.BackButton.show(); } else { tg.BackButton.hide(); }
    } catch (e) { /* */ }
  }
  try {
    const [p0, p1] = parts;
    switch (p0) {
      case undefined: await renderHome(el); break;
      case "games": await renderGames(el, params); break;
      case "game": await renderGame(el, p1); break;
      case "search": await renderSearch(el, params); break;
      case "cart": await renderCart(el); break;
      case "wallet": await renderWallet(el, params); break;
      case "login": renderAuth(el, "login", params); break;
      case "register": renderAuth(el, "register", params); break;
      case "reset": renderReset(el, params); break;
      case "account": await renderAccount(el); break;
      case "orders": await renderOrders(el, params); break;
      case "order": await renderOrder(el, p1); break;
      case "notifications": await renderNotifications(el); break;
      case "market": await renderMarket(el, params); break;
      case "support": await renderSupport(el); break;
      case "admin": {
        const { renderAdmin } = await import("./admin.js");
        await renderAdmin(el, parts, params);
        break;
      }
      default: await renderHome(el);
    }
  } catch (e) {
    if (seq === routeSeq) el.innerHTML = notice(esc(t("load_failed")), "err");
    console.error(e);
  }
}

async function telegramLogin() {
  try {
    const res = await api("/api/auth/telegram", { method: "POST", body: { init_data: tg.initData } });
    if (res.token) setToken(res.token);
    return res.user;
  } catch (e) {
    return null;
  }
}

async function boot() {
  initTheme();
  if (tg) {
    document.documentElement.classList.add("tg");
    try {
      tg.ready();
      tg.expand();
      tg.BackButton.onClick(() => { if (history.length > 1) history.back(); else location.hash = "#/"; });
    } catch (e) { /* */ }
    // default language from Telegram if the user has not chosen one yet
    try {
      if (!localStorage.getItem("vyron_lang")) {
        const code = ((tg.initDataUnsafe || {}).user || {}).language_code || "";
        if (["uz", "en", "ru"].includes(code.slice(0, 2))) setLang(code.slice(0, 2));
      }
    } catch (e) { /* */ }
  }
  document.documentElement.lang = getLang();
  const [cfg, me] = await Promise.all([
    api("/api/config").catch(() => ({ branding: {} })),
    api("/api/auth/me").catch(() => null),
  ]);
  state.config = cfg;
  state.user = me && me.user ? me.user : null;
  if (tg && tg.initData) {
    const user = await telegramLogin();
    if (user) state.user = user;
  }
  if (state.user) {
    let stored = null;
    try { stored = localStorage.getItem("vyron_theme"); } catch (e) { /* */ }
    if (!stored && state.user.theme) applyTheme(state.user.theme);
    try {
      if (!localStorage.getItem("vyron_lang") && state.user.language) setLang(state.user.language);
    } catch (e) { /* */ }
  }
  renderShell();
  bindShellEvents(route);
  await route();
  refreshBadges();
  setInterval(() => { if (!document.hidden) refreshBadges(); }, 60000);
}

boot();
