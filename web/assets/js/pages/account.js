/* Auth, account, orders, order detail, notifications, password. */
import {
  api, state, esc, money, t, icon, toast, modal, loading, empty, notice, pageHead, navigate, requireLogin,
  errMsg, withBusy, dateTime, statusPill, setToken, getLang, tg, copyText,
} from "../core.js";
import { renderShell, refreshBadges, setTheme, getTheme, changeLang } from "../shell.js";

/* ---------------- LOGIN / REGISTER ---------------- */
export function renderAuth(view, mode, params) {
  if (state.user) { navigate(params.next || "#/account"); return; }
  const isReg = mode === "register";
  view.innerHTML = `<div class="auth-wrap">
    <div class="card stack">
      <div class="center"><img src="/assets/img/logo.svg" alt="" style="width:64px;height:64px;margin:0 auto 8px"><h2>${esc(t(isReg ? "create_account" : "welcome_back"))}</h2></div>
      <div class="tabs"><button class="${!isReg ? "active" : ""}" data-m="login">${esc(t("login"))}</button><button class="${isReg ? "active" : ""}" data-m="register">${esc(t("register"))}</button></div>
      <form id="auth-form" class="stack" autocomplete="on">
        <label class="field"><span>${esc(t(isReg ? "username" : "username_or_email"))}</span><input class="input" name="username" required minlength="3" maxlength="64" autocomplete="username"></label>
        ${isReg ? `<label class="field"><span>${esc(t("email"))}</span><input class="input" name="email" type="email" required autocomplete="email"></label>` : ""}
        <label class="field"><span>${esc(t("password"))}</span><input class="input" name="password" type="password" required minlength="${isReg ? 8 : 1}" maxlength="128" autocomplete="${isReg ? "new-password" : "current-password"}"></label>
        ${isReg ? `<p class="help">${esc(t("password_rules"))}</p>` : ""}
        <div class="form-error" id="auth-err"></div>
        <button class="btn primary block">${esc(t(isReg ? "register" : "login"))}</button>
      </form>
      ${!isReg ? `<button class="btn ghost sm" id="forgot" style="align-self:center">${esc(t("forgot_password"))}</button>` : ""}
      <p class="help center">${icon("telegram", "sm")} ${esc(t("tg_login_hint"))}</p>
    </div></div>`;
  view.querySelectorAll("[data-m]").forEach((b) => b.addEventListener("click", () =>
    navigate(`#/${b.dataset.m}${params.next ? "?next=" + encodeURIComponent(params.next) : ""}`)));
  const form = view.querySelector("#auth-form");
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const err = view.querySelector("#auth-err");
    err.textContent = "";
    withBusy(form.querySelector("button"), async () => {
      try {
        const body = { username: form.username.value.trim(), password: form.password.value };
        if (isReg) { body.email = form.email.value.trim(); body.language = getLang(); }
        const res = await api(`/api/auth/${isReg ? "register" : "login"}`, { method: "POST", body });
        if (res.token) setToken(res.token);
        state.user = res.user;
        renderShell();
        refreshBadges();
        toast(t(isReg ? "account_created" : "logged_in"));
        navigate(params.next || "#/");
      } catch (ex) { err.textContent = errMsg(ex); }
    });
  });
  const fb = view.querySelector("#forgot");
  if (fb) fb.addEventListener("click", () => {
    const m = modal(t("forgot_password"), `<form class="stack"><p class="small muted">${esc(t("forgot_d"))}</p>
      <label class="field"><span>${esc(t("email"))}</span><input class="input" name="email" type="email" required></label>
      <button class="btn primary block">${esc(t("send"))}</button></form>`);
    const f = m.el.querySelector("form");
    f.addEventListener("submit", (e) => {
      e.preventDefault();
      withBusy(f.querySelector("button"), async () => {
        try { await api("/api/auth/password/forgot", { method: "POST", body: { email: f.email.value.trim() } }); m.close(); toast(t("reset_sent"), "info"); }
        catch (ex) { toast(errMsg(ex), "err"); }
      });
    });
  });
}

export function renderReset(view, params) {
  view.innerHTML = `<div class="auth-wrap"><div class="card stack"><h2>${esc(t("new_password"))}</h2>
    <form class="stack"><label class="field"><span>${esc(t("new_password"))}</span><input class="input" name="password" type="password" minlength="8" required autocomplete="new-password"></label>
    <p class="help">${esc(t("password_rules"))}</p><button class="btn primary block">${esc(t("save"))}</button></form></div></div>`;
  const f = view.querySelector("form");
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    withBusy(f.querySelector("button"), async () => {
      try { await api("/api/auth/password/reset", { method: "POST", body: { token: params.token || "", password: f.password.value } }); toast(t("password_changed")); navigate("#/login"); }
      catch (ex) { toast(errMsg(ex), "err"); }
    });
  });
}

/* ---------------- ACCOUNT ---------------- */
export async function renderAccount(view) {
  if (!requireLogin()) return;
  const u = state.user;
  const initial = (u.username || "V").slice(0, 1).toUpperCase();
  view.innerHTML = `${pageHead(t("nav_profile"))}
    <div class="grid g2">
      <div class="stack">
        <div class="card row">
          <div class="avatar">${esc(initial)}</div>
          <div class="grow"><h3 style="margin:0" class="ellipsis">${esc(u.username)}</h3>
            <div class="small muted ellipsis">${esc(u.email || (u.telegram_id ? "Telegram ID " + u.telegram_id : ""))}</div>
            <div class="row wrap" style="margin-top:6px;gap:6px">${u.is_admin ? `<span class="pill info">${icon("shield", "sm")} ${esc(t("admin"))}</span>` : ""}
            ${u.telegram_linked ? `<span class="pill ok">${icon("telegram", "sm")} Telegram</span>` : ""}</div></div>
        </div>
        <a class="card balance-card row" href="#/wallet" style="color:#fff">
          <div class="grow"><div class="small muted">${esc(t("your_balance"))}</div><div style="font-size:26px;font-weight:800">${money(u.balance)}</div></div>
          <span class="btn sm" style="background:rgba(255,255,255,.14);color:#fff;box-shadow:none">${icon("plus", "sm")} ${esc(t("top_up"))}</span>
        </a>
        <div class="card menu-list">
          <a href="#/orders"><span class="ic">${icon("orders")}</span><span class="grow">${esc(t("my_orders"))}</span>${icon("right", "sm")}</a>
          <a href="#/notifications"><span class="ic">${icon("bell")}</span><span class="grow">${esc(t("notifications"))}</span>${icon("right", "sm")}</a>
          <a href="#/market"><span class="ic">${icon("store")}</span><span class="grow">${esc(t("nav_market"))}</span>${icon("right", "sm")}</a>
          <a href="#/support"><span class="ic">${icon("headset")}</span><span class="grow">${esc(t("nav_support"))}</span>${icon("right", "sm")}</a>
          ${u.is_admin ? `<a href="#/admin"><span class="ic">${icon("settings")}</span><span class="grow">${esc(t("nav_admin"))}</span>${icon("right", "sm")}</a>` : ""}
        </div>
      </div>
      <div class="stack">
        <div class="card stack">
          <h3>${esc(t("settings"))}</h3>
          <div class="row between"><span>${icon("globe")} ${esc(t("language"))}</span>
            <div class="chips" style="padding:0">${["uz", "en", "ru"].map((l) => `<button class="chip ${getLang() === l ? "active" : ""}" data-l="${l}">${l.toUpperCase()}</button>`).join("")}</div></div>
          <div class="row between"><span>${icon(getTheme() === "day" ? "sun" : "moon")} ${esc(t("theme"))}</span>
            <div class="chips" style="padding:0"><button class="chip ${getTheme() === "day" ? "active" : ""}" data-th="day">${esc(t("day"))}</button><button class="chip ${getTheme() === "night" ? "active" : ""}" data-th="night">${esc(t("night"))}</button></div></div>
        </div>
        <div class="card menu-list">
          <button id="pw-btn"><span class="ic">${icon("lock")}</span><span class="grow">${esc(t(u.has_password ? "change_password" : "set_password"))}</span>${icon("right", "sm")}</button>
          ${!tg ? `<button id="logout" style="color:var(--err)"><span class="ic" style="color:var(--err)">${icon("logout")}</span><span class="grow">${esc(t("logout"))}</span></button>` : ""}
        </div>
        ${!u.has_password && u.telegram_linked ? notice(esc(t("set_password_hint")), "info", "info") : ""}
      </div>
    </div>`;
  view.querySelectorAll("[data-l]").forEach((b) => b.addEventListener("click", () => changeLang(b.dataset.l)));
  view.querySelectorAll("[data-th]").forEach((b) => b.addEventListener("click", () => { setTheme(b.dataset.th); renderShell(); renderAccount(view); }));
  view.querySelector("#pw-btn").addEventListener("click", () => passwordModal());
  const lo = view.querySelector("#logout");
  if (lo) lo.addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    setToken(null);
    state.user = null; state.cartCount = 0; state.unread = 0;
    renderShell();
    navigate("#/");
  });
}

function passwordModal() {
  const u = state.user;
  const m = modal(t(u.has_password ? "change_password" : "set_password"), `<form class="stack">
    ${u.has_password ? `<label class="field"><span>${esc(t("current_password"))}</span><input class="input" type="password" name="current_password" required autocomplete="current-password"></label>` : ""}
    ${!u.has_password && !u.email ? `<p class="small muted">${esc(t("web_login_username", { username: u.username }))}</p>` : ""}
    <label class="field"><span>${esc(t("new_password"))}</span><input class="input" type="password" name="new_password" required minlength="8" autocomplete="new-password"></label>
    <p class="help">${esc(t("password_rules"))}</p>
    <button class="btn primary block">${esc(t("save"))}</button></form>`);
  const f = m.el.querySelector("form");
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    withBusy(f.querySelector("button"), async () => {
      try {
        const body = { new_password: f.new_password.value };
        if (f.current_password) body.current_password = f.current_password.value;
        await api("/api/account/password", { method: "POST", body });
        state.user.has_password = true;
        m.close();
        toast(t("password_changed"));
      } catch (ex) { toast(errMsg(ex), "err"); }
    });
  });
}

/* ---------------- ORDERS ---------------- */
const ORDER_FILTERS = ["", "PENDING_PAYMENT", "SUPPLIER_PROCESSING", "COMPLETED", "FAILED", "REFUNDED"];
export async function renderOrders(view, params) {
  if (!requireLogin()) return;
  const status = params.status || "";
  view.innerHTML = `${pageHead(t("my_orders"), { back: true })}
    <div class="chips">${ORDER_FILTERS.map((s) => `<button class="chip ${s === status ? "active" : ""}" data-s="${s}">${esc(s ? t("st_" + s) : t("all"))}</button>`).join("")}</div>
    <div id="o-list">${loading()}</div>`;
  view.querySelectorAll("[data-s]").forEach((b) => b.addEventListener("click", () => navigate(`#/orders${b.dataset.s ? "?status=" + b.dataset.s : ""}`)));
  try {
    const r = await api(`/api/orders?page_size=50${status ? "&status=" + status : ""}`);
    view.querySelector("#o-list").innerHTML = r.items.length ? `<div class="card list">${r.items.map((o) => `
      <a class="li" href="#/order/${encodeURIComponent(o.order_number)}" style="color:var(--text)">
        <div class="tx-ic" style="color:var(--primary-2)">${icon("box")}</div>
        <div class="grow"><b class="ellipsis" style="display:block">${esc(o.items.map((i) => i.variant_name).join(", "))}</b>
        <div class="tiny muted">${esc(o.order_number)} · ${dateTime(o.created_at)}</div></div>
        <div style="text-align:right"><b>${money(o.total)}</b><div>${statusPill(o.status)}</div></div></a>`).join("")}</div>`
      : `<div class="card">${empty("orders", t("no_orders"), "", `<a class="btn primary" href="#/games">${esc(t("browse_games"))}</a>`)}</div>`;
  } catch (e) { view.querySelector("#o-list").innerHTML = notice(esc(errMsg(e)), "err"); }
}

const FLOW = ["PENDING_PAYMENT", "PAID", "SUPPLIER_PROCESSING", "COMPLETED"];
let orderPoll = null;
export async function renderOrder(view, number) {
  if (orderPoll) { clearTimeout(orderPoll); orderPoll = null; }
  if (!requireLogin()) return;
  let o;
  try { o = (await api(`/api/orders/${encodeURIComponent(number)}`)).order; }
  catch (e) { view.innerHTML = pageHead(t("order"), { back: true }) + empty("orders", t("order_not_found")); return; }
  const bad = ["FAILED", "CANCELLED", "REFUNDED"].includes(o.status);
  const idx = o.status === "FULFILLMENT_PENDING" ? 2 : FLOW.indexOf(o.status);
  view.innerHTML = `${pageHead(t("order"), { back: true })}
    <div class="grid g2">
      <div class="card stack">
        <div class="row between"><span class="mono small">${esc(o.order_number)}</span>
          <button class="btn sm ghost" data-copy>${icon("copy", "sm")}</button></div>
        <div>${statusPill(o.status)}</div>
        <div class="steps">${FLOW.map((_, i) => `<span class="${bad ? (i === 0 ? "bad" : "") : i <= idx ? "on" : ""}"></span>`).join("")}</div>
        <p class="small muted">${esc(t("st_d_" + o.status))}</p>
        ${o.status === "PENDING_PAYMENT" ? notice(esc(t("pending_payment_hint")), "info", "info") : ""}
        ${o.status === "REFUNDED" ? notice(esc(t("refunded_hint")), "info", "wallet") : ""}
      </div>
      <div class="card stack">
        ${o.items.map((i) => `<div class="row"><div class="grow"><b>${esc(i.variant_name)}</b><div class="small muted">${esc(i.game_name || i.product_name)}</div>
          ${i.player_info ? `<div class="tiny muted">${esc(Object.entries(i.player_info).map(([k, v]) => `${k}: ${v}`).join(" · "))}</div>` : ""}</div><b>${money(i.line_total)}</b></div>`).join("")}
        <div class="inset" style="padding:10px 14px">
          <div class="sum-row"><span class="muted">${esc(t("subtotal"))}</span><span>${money(o.subtotal)}</span></div>
          ${o.discount ? `<div class="sum-row"><span class="muted">${esc(t("discount"))}${o.coupon_code ? " (" + esc(o.coupon_code) + ")" : ""}</span><span>− ${money(o.discount)}</span></div>` : ""}
          <div class="sum-row total"><span>${esc(t("total"))}</span><span>${money(o.total)}</span></div>
        </div>
        <div class="kv small"><span>${esc(t("created"))}</span><span>${dateTime(o.created_at)}</span>
          ${o.paid_at ? `<span>${esc(t("paid_at"))}</span><span>${dateTime(o.paid_at)}</span>` : ""}
          ${o.completed_at ? `<span>${esc(t("completed_at"))}</span><span>${dateTime(o.completed_at)}</span>` : ""}</div>
        <a class="btn block" href="#/support">${icon("headset")} ${esc(t("need_help"))}</a>
      </div>
    </div>`;
  view.querySelector("[data-copy]").addEventListener("click", () => copyText(o.order_number));
  if (["PAID", "FULFILLMENT_PENDING", "SUPPLIER_PROCESSING"].includes(o.status)) {
    orderPoll = setTimeout(() => { if (document.body.contains(view) && location.hash.includes(number)) renderOrder(view, number); }, 5000);
  }
}

/* ---------------- NOTIFICATIONS ---------------- */
export async function renderNotifications(view) {
  if (!requireLogin()) return;
  view.innerHTML = pageHead(t("notifications"), { back: true }) + loading();
  try {
    const r = await api(`/api/account/notifications?lang=${getLang()}`);
    view.innerHTML = pageHead(t("notifications"), { back: true }) + (r.items.length
      ? `<div class="card list">${r.items.map((n) => `<div class="li" style="${n.is_read ? "opacity:.75" : ""}">
          <div class="tx-ic" style="color:var(--primary-2)">${icon(n.kind === "admin" ? "bell" : n.kind === "wallet" ? "wallet" : "box")}</div>
          <div class="grow"><b>${esc(n.title)}</b><div class="small muted">${esc(n.body)}</div><div class="tiny muted">${dateTime(n.created_at)}</div></div>
          ${n.is_read ? "" : `<span class="pill info">${esc(t("new"))}</span>`}</div>`).join("")}</div>`
      : `<div class="card">${empty("bell", t("no_notifications"))}</div>`);
    if (r.items.some((n) => !n.is_read)) {
      await api("/api/account/notifications/read", { method: "POST" }).catch(() => {});
      state.unread = 0;
      refreshBadges();
    }
  } catch (e) { view.innerHTML = pageHead(t("notifications"), { back: true }) + notice(esc(errMsg(e)), "err"); }
}
