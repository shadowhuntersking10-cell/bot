/* Storefront: home, games, game detail (buy / add to cart), search. */
import {
  api, state, esc, safeUrl, money, t, icon, toast, modal, loading, empty, notice, pageHead,
  navigate, requireLogin, errMsg, withBusy, idemKey, localized, FALLBACK_IMG, ApiError, som,
} from "../core.js";
import { refreshBadges } from "../shell.js";

function img(url, label = "") {
  return safeUrl(url, FALLBACK_IMG + encodeURIComponent(label));
}

export function gameTile(g) {
  const avail = g.available !== false;
  return `<a class="tile" href="#/game/${encodeURIComponent(g.slug)}">
    <div class="thumb"><img loading="lazy" src="${img(g.icon_url, g.currency_label)}" alt="${esc(g.currency_label || g.name)}"></div>
    <div class="name ellipsis">${esc(g.name)}</div>
    <div class="sub"><span class="ellipsis">${esc(g.currency_label || t("digital"))}</span>
    ${avail ? `<span class="pill ok">${esc(t("in_stock"))}</span>` : `<span class="pill">${esc(t("soon"))}</span>`}</div>
  </a>`;
}

/* ---------------- HOME ---------------- */
export async function renderHome(view) {
  const b = (state.config && state.config.branding) || {};
  const [games, promos] = await Promise.all([
    api("/api/games?page_size=100").catch(() => ({ items: [] })),
    api("/api/promotions").catch(() => ({ items: [] })),
  ]);
  const items = games.items || [];
  const featured = items.filter((g) => g.featured).slice(0, 12);
  const available = items.filter((g) => g.available);
  const heroImgs = b.hero_image_url
    ? `<img class="single" src="${safeUrl(b.hero_image_url)}" alt="">`
    : ["/assets/img/currency/uc.webp", "/assets/img/currency/ml-diamonds.webp", "/assets/img/currency/robux.webp"]
        .map((u) => `<img src="${u}" alt="">`).join("");
  const announce = localized(b, "announcement");
  view.innerHTML = `
    ${announce ? `<div class="card announce">${icon("bell")}<div>${esc(announce)}</div></div>` : ""}
    <section class="hero">
      <div class="glow"></div>
      <div style="position:relative">
        <span class="pill info" style="background:rgba(124,196,255,.16);color:#bfe3ff">${icon("bolt", "sm")} ${esc(t("hero_badge"))}</span>
        <h1 style="margin-top:12px">${esc(localized(b, "hero_title") || t("hero_title"))}</h1>
        <p>${esc(localized(b, "hero_subtitle") || t("hero_sub"))}</p>
        <div class="row wrap" style="margin-top:18px">
          <a class="btn primary" href="#/games">${icon("games")} ${esc(t("browse_games"))}</a>
          <a class="btn" href="#/wallet">${icon("wallet")} ${esc(t("top_up_balance"))}</a>
        </div>
      </div>
      <div class="hero-art">${heroImgs}</div>
    </section>

    <section class="section">
      <div class="features">
        ${[["bolt", "f_fast", "f_fast_d"], ["shield", "f_secure", "f_secure_d"], ["card", "f_cards", "f_cards_d"], ["headset", "f_support", "f_support_d"]]
          .map(([i, a, d]) => `<div class="card feature"><div class="ic">${icon(i)}</div><div><b>${esc(t(a))}</b><div class="small muted">${esc(t(d))}</div></div></div>`).join("")}
      </div>
    </section>

    ${(promos.items || []).length ? `<section class="section"><div class="section-head"><h2>${esc(t("promotions"))}</h2></div>
      <div class="grid g3">${promos.items.map((p) => `<div class="card row top">
        ${p.image_url ? `<img src="${safeUrl(p.image_url)}" style="width:64px;height:64px;border-radius:14px;object-fit:cover" alt="">` : `<div class="empty" style="padding:0"><div class="ic" style="margin:0">${icon("gift")}</div></div>`}
        <div class="grow"><b>${esc(p.title)}</b><div class="small muted">${esc(p.description || "")}</div>
        ${p.coupon_code ? `<div class="pill info mono" style="margin-top:6px">${esc(p.coupon_code)}</div>` : ""}</div></div>`).join("")}</div></section>` : ""}

    <section class="section">
      <div class="section-head"><h2>${esc(t("popular_games"))}</h2><a href="#/games" class="small">${esc(t("see_all"))} ${icon("right", "sm")}</a></div>
      ${featured.length ? `<div class="tiles">${featured.map(gameTile).join("")}</div>` : empty("games", t("no_games"))}
    </section>

    ${available.length === 0 ? `<section class="section">${notice(esc(t("catalog_not_ready")), "info", "info")}</section>` : ""}
  `;
}

/* ---------------- GAMES ---------------- */
const CATS = ["all", "mobile", "pc", "console", "gift-cards"];
export async function renderGames(view, params) {
  const cat = params.cat || "all";
  const q = (params.q || "").trim();
  view.innerHTML = `${pageHead(t("nav_games"))}
    <div class="search" style="max-width:none;margin:0 0 12px">${icon("search")}<input class="input" id="g-q" type="search" value="${esc(q)}" placeholder="${esc(t("search_games"))}"></div>
    <div class="chips">${CATS.map((c) => `<button class="chip ${c === cat ? "active" : ""}" data-cat="${c}">${esc(t("cat_" + c))}</button>`).join("")}</div>
    <div id="g-list">${loading()}</div>`;
  const data = await api("/api/games?page_size=100").catch(() => ({ items: [] }));
  const list = view.querySelector("#g-list");
  const draw = () => {
    const qq = view.querySelector("#g-q").value.trim().toLowerCase();
    const active = view.querySelector(".chip.active").dataset.cat;
    const items = (data.items || []).filter((g) =>
      (active === "all" || g.category === active) &&
      (!qq || g.name.toLowerCase().includes(qq) || (g.currency_label || "").toLowerCase().includes(qq)));
    items.sort((a, b) => Number(b.available) - Number(a.available));
    list.innerHTML = items.length ? `<div class="tiles">${items.map(gameTile).join("")}</div>` : empty("search", t("nothing_found"));
  };
  view.querySelectorAll("[data-cat]").forEach((b) => b.addEventListener("click", () => {
    view.querySelectorAll("[data-cat]").forEach((x) => x.classList.toggle("active", x === b));
    draw();
  }));
  view.querySelector("#g-q").addEventListener("input", draw);
  draw();
}

/* ---------------- SEARCH ---------------- */
export async function renderSearch(view, params) {
  const q = (params.q || "").trim();
  view.innerHTML = `${pageHead(t("search"), { back: true })}
    <form id="s-form" class="search" style="max-width:none;margin-bottom:16px">${icon("search")}<input class="input" name="q" type="search" value="${esc(q)}" placeholder="${esc(t("search_placeholder"))}" autofocus></form>
    <div id="s-res">${q ? loading() : ""}</div>`;
  view.querySelector("#s-form").addEventListener("submit", (e) => {
    e.preventDefault();
    navigate(`#/search?q=${encodeURIComponent(e.target.q.value.trim())}`);
  });
  if (!q) return;
  const res = await api(`/api/search?q=${encodeURIComponent(q)}`).catch(() => ({ games: [], products: [] }));
  const games = res.games || [];
  const out = view.querySelector("#s-res");
  out.innerHTML = games.length ? `<div class="tiles">${games.map(gameTile).join("")}</div>` : empty("search", t("nothing_found"));
}

/* ---------------- GAME ---------------- */
function fieldsFor(product) {
  const f = (product.required_fields || []).filter((x) => x && x.key);
  if (f.length) return f;
  if (product.product_type === "TOPUP") return [{ key: "player_id", label: "", type: "string", required: true }];
  return [];
}
function fieldLabel(f) {
  const k = String(f.key).toLowerCase();
  if (/player|user|uid|account|id$/.test(k) && !f.label) return t("player_id");
  if (k === "player_id") return t("player_id");
  if (k === "zone_id" || k === "server_id" || k === "server") return t("server_id");
  return f.label || f.key;
}

export async function renderGame(view, slug) {
  let data;
  try {
    data = await api(`/api/games/${encodeURIComponent(slug)}`);
  } catch (e) {
    view.innerHTML = pageHead(t("nav_games"), { back: true }) + empty("games", t("game_not_found"));
    return;
  }
  const g = data.game;
  const products = (g.products || []).filter((p) => p.variants && p.variants.length);
  const allVariants = [];
  products.forEach((p) => p.variants.forEach((v) => allVariants.push({ ...v, product: p })));
  allVariants.sort((a, b) => Number(b.available) - Number(a.available) || (a.price || 0) - (b.price || 0));
  const anyAvail = allVariants.some((v) => v.available);

  view.innerHTML = `${pageHead(g.name, { back: true })}
    <div class="card game-head">
      <img src="${img(g.logo_url || g.icon_url, g.currency_label)}" alt="${esc(g.currency_label || g.name)}">
      <div>
        <h2 style="margin-bottom:6px">${esc(g.name)}</h2>
        <div class="row wrap">
          ${g.currency_label ? `<span class="pill info">${icon("coins", "sm")} ${esc(g.currency_label)}</span>` : ""}
          ${anyAvail ? `<span class="pill ok">${icon("bolt", "sm")} ${esc(t("instant_delivery"))}</span>` : `<span class="pill">${esc(t("not_available"))}</span>`}
        </div>
        ${g.description ? `<p class="muted small" style="margin-top:8px">${esc(g.description)}</p>` : ""}
      </div>
    </div>
    <section class="section">
      <div class="section-head"><h2>${esc(t("choose_package"))}</h2></div>
      ${allVariants.length ? `<div class="variants">${allVariants.map((v) => `
        <button class="variant" data-v="${v.id}" ${v.available ? "" : "disabled"}>
          <img src="${img(v.product.image_url || g.icon_url, g.currency_label)}" alt="">
          <div class="grow"><div class="v-name">${esc(v.amount_label || v.name)}</div>
          <div class="v-price">${v.available ? money(v.price, v.currency) : esc(t("not_available"))}</div></div>
        </button>`).join("")}</div>`
        : notice(`<b>${esc(t("not_available"))}</b><div class="small muted">${esc(t("game_not_ready"))}</div>`, "info", "info")}
    </section>
    <div id="buy-area"></div>`;

  const area = view.querySelector("#buy-area");
  let selected = null;
  view.querySelectorAll(".variant[data-v]").forEach((btn) => btn.addEventListener("click", () => {
    view.querySelectorAll(".variant").forEach((b) => b.classList.toggle("sel", b === btn));
    selected = allVariants.find((v) => String(v.id) === btn.dataset.v);
    drawBuy();
  }));

  function drawBuy() {
    if (!selected) { area.innerHTML = ""; return; }
    const fields = fieldsFor(selected.product);
    area.innerHTML = `<div class="card stack">
      <h3>${esc(t("player_details"))}</h3>
      <form id="pi-form" class="grid g2">${fields.map((f) => `<label class="field"><span>${esc(fieldLabel(f))}${f.required ? " *" : ""}</span>
        <input class="input" name="${esc(f.key)}" ${f.required ? "required" : ""} autocomplete="off" maxlength="64" inputmode="${f.type === "number" ? "numeric" : "text"}"></label>`).join("")}</form>
      ${fields.length ? `<p class="help">${esc(t("player_hint"))}</p>` : ""}
    </div>
    <div class="card buy-bar">
      <div class="grow"><div class="small muted ellipsis">${esc(selected.amount_label || selected.name)}</div><b style="font-size:18px">${money(selected.price, selected.currency)}</b></div>
      <button class="btn" id="add-cart" aria-label="${esc(t("add_to_cart"))}">${icon("cart")}<span class="desk-only">${esc(t("add_to_cart"))}</span></button>
      <button class="btn primary" id="buy-now">${icon("bolt")} ${esc(t("buy_now"))}</button>
    </div>`;
    const form = area.querySelector("#pi-form");
    const collect = () => {
      if (!form.reportValidity()) return null;
      const info = {};
      for (const el of form.elements) if (el.name && el.value.trim()) info[el.name] = el.value.trim();
      return info;
    };
    area.querySelector("#add-cart").addEventListener("click", async (e) => {
      if (!requireLogin()) return;
      const info = collect(); if (!info) return;
      await withBusy(e.currentTarget, async () => {
        try {
          await api("/api/cart/items", { method: "POST", body: { variant_id: selected.id, quantity: 1, player_info: info } });
          toast(t("added_to_cart"));
          refreshBadges();
        } catch (err) { toast(errMsg(err), "err"); }
      });
    });
    area.querySelector("#buy-now").addEventListener("click", () => {
      if (!requireLogin()) return;
      const info = collect(); if (!info) return;
      openPayModal({ variant: selected, info, gameName: g.name });
    });
    area.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

/* -------- payment chooser (shared by "Buy now" and cart checkout) -------- */
export function openPayModal({ variant, info, gameName, cart }) {
  const cfg = state.config || {};
  const total = cart ? cart.subtotal : variant.price;
  const units = cart ? cart.count : 1;
  const balance = (state.user && state.user.balance) || 0;
  const providerOk = cfg.provider_payment_configured && units === 1;
  const enough = balance >= total;
  const lines = cart
    ? cart.items.map((i) => `<div class="sum-row"><span class="ellipsis">${esc(i.variant ? i.variant.amount_label || i.variant.name : "")} × ${i.quantity}</span><b>${money(i.line_total)}</b></div>`).join("")
    : `<div class="sum-row"><span>${esc(gameName)} — ${esc(variant.amount_label || variant.name)}</span><b>${money(variant.price)}</b></div>`;
  const m = modal(t("checkout"), `
    <div class="stack">
      <div class="inset" style="padding:12px 14px">${lines}
        <div id="disc-row" class="sum-row hidden"><span>${esc(t("discount"))}</span><b id="disc-val"></b></div>
        <div class="sum-row total"><span>${esc(t("total"))}</span><span id="pay-total">${money(total)}</span></div>
      </div>
      ${units === 1 ? `<form id="cp-form" class="row"><input class="input" name="code" placeholder="${esc(t("coupon_code"))}" maxlength="64" style="text-transform:uppercase"><button class="btn">${esc(t("apply"))}</button></form>` : `<p class="help">${esc(t("coupon_single_hint"))}</p>`}
      <div>
        <div class="small muted" style="margin:0 0 8px 4px;font-weight:600">${esc(t("payment_method"))}</div>
        <label class="card flat inset row" style="cursor:pointer;padding:12px">
          <input type="radio" name="pm" value="balance" checked style="accent-color:var(--primary)">
          <div class="grow"><b>${esc(t("pay_with_balance"))}</b><div class="small muted">${esc(t("your_balance"))}: ${money(balance)}</div></div>${icon("wallet")}
        </label>
        ${providerOk ? `<label class="card flat inset row" style="cursor:pointer;padding:12px;margin-top:10px">
          <input type="radio" name="pm" value="provider" style="accent-color:var(--primary)">
          <div class="grow"><b>${esc(t("pay_with_card"))}</b><div class="small muted">${esc(t("pay_with_card_d"))}</div></div>${icon("card")}</label>` : ""}
      </div>
      <div id="pay-warn">${!enough ? notice(`${esc(t("insufficient_balance_short"))} <b>${money(total - balance)}</b>. <a href="#/wallet?need=${Math.ceil((total - balance) / 100)}" data-close>${esc(t("top_up_balance"))}</a>`, "", "wallet") : ""}</div>
      <p class="help">${icon("shield", "sm")} ${esc(t("price_rechecked"))}</p>
      <button class="btn primary block" id="pay-go">${icon("check")} ${esc(t("confirm_and_pay"))}</button>
    </div>`);
  let coupon = null;
  const key = idemKey();
  const cp = m.el.querySelector("#cp-form");
  if (cp) cp.addEventListener("submit", async (e) => {
    e.preventDefault();
    const code = cp.code.value.trim().toUpperCase();
    if (!code) return;
    if (!variant) { coupon = code; return; }
    // validate coupon through the cart validator for this single unit
    try {
      const res = await api("/api/cart/coupon", { method: "POST", body: { code, variant_id: variant.id } });
      coupon = code;
      const disc = res.discount || 0;
      m.el.querySelector("#disc-row").classList.remove("hidden");
      m.el.querySelector("#disc-val").textContent = "− " + money(disc);
      m.el.querySelector("#pay-total").textContent = money(Math.max(0, total - disc));
      toast(t("coupon_applied"));
    } catch (err) { coupon = null; toast(errMsg(err), "err"); }
  });
  m.el.querySelector("#pay-go").addEventListener("click", (e) => withBusy(e.currentTarget, async () => {
    const method = (m.el.querySelector("input[name=pm]:checked") || {}).value || "balance";
    const body = { payment_method: method, idempotency_key: key, coupon_code: coupon || undefined };
    try {
      let res;
      if (cart) res = await api("/api/checkout", { method: "POST", body: { ...body, player_info: {} } });
      else res = await api("/api/checkout/quick", { method: "POST", body: { ...body, variant_id: variant.id, player_info: info } });
      m.close();
      if (typeof res.balance === "number" && state.user) state.user.balance = res.balance;
      refreshBadges();
      if (res.checkout_url) { window.location.href = res.checkout_url; return; }
      if (res.error) { toast(errMsg(new ApiError(400, res.error)), "err"); navigate(`#/order/${res.order.order_number}`); return; }
      toast(t("order_placed"));
      if (res.orders && res.orders.length > 1) navigate("#/orders");
      else navigate(`#/order/${res.order.order_number}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 402 && err.detail && err.detail.missing) {
        m.el.querySelector("#pay-warn").innerHTML = notice(`${esc(t("insufficient_balance_short"))} <b>${money(err.detail.missing)}</b>. <a href="#/wallet?need=${Math.ceil(err.detail.missing / 100)}">${esc(t("top_up_balance"))}</a>`, "err", "wallet");
        m.el.querySelector("#pay-warn a").addEventListener("click", () => m.close());
      } else toast(errMsg(err), "err");
    }
  }));
  const wl = m.el.querySelector("#pay-warn a");
  if (wl) wl.addEventListener("click", () => m.close());
  return m;
}
export { som };
