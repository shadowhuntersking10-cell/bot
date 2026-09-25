/* Cart page (server-side cart, prices revalidated on every load). */
import { api, state, esc, safeUrl, money, t, icon, toast, loading, empty, notice, pageHead, requireLogin, errMsg, FALLBACK_IMG } from "../core.js";
import { refreshBadges } from "../shell.js";
import { openPayModal } from "./shop.js";

export async function renderCart(view) {
  if (!requireLogin()) return;
  view.innerHTML = pageHead(t("nav_cart")) + loading();
  let cart;
  try {
    cart = (await api("/api/cart/validate", { method: "POST" })).cart;
  } catch (e) {
    view.innerHTML = pageHead(t("nav_cart")) + notice(esc(errMsg(e)), "err");
    return;
  }
  draw(view, cart);
}

function draw(view, cart) {
  state.cartCount = cart.count || 0;
  if (!cart.items.length) {
    view.innerHTML = pageHead(t("nav_cart")) + `<div class="card">${empty("cart", t("cart_empty"), t("cart_empty_d"), `<a class="btn primary" href="#/games">${icon("games")} ${esc(t("browse_games"))}</a>`)}</div>`;
    refreshBadges();
    return;
  }
  const tooMany = cart.items.some((i) => i.quantity > 10);
  view.innerHTML = `${pageHead(t("nav_cart"), { extra: `<button class="btn sm ghost danger" id="c-clear">${icon("trash", "sm")} ${esc(t("clear"))}</button>` })}
    <div class="grid" style="grid-template-columns:minmax(0,1fr);gap:16px">
      <div class="card list">${cart.items.map((i) => `
        <div class="li" data-item="${i.id}">
          <img class="thumb-sm" src="${safeUrl(i.product && i.product.image_url, FALLBACK_IMG)}" alt="">
          <div class="grow">
            <div class="ellipsis"><b>${esc(i.variant ? i.variant.amount_label || i.variant.name : "—")}</b></div>
            <div class="small muted ellipsis">${esc(i.game ? i.game.name : "")}${i.player_info ? " · " + esc(Object.values(i.player_info).join(" / ")) : ""}</div>
            ${i.available ? `<div class="small"><b>${money(i.unit_price)}</b></div>` : `<span class="pill err">${esc(t("not_available"))}</span>`}
          </div>
          <div class="qty"><button data-dec aria-label="-">${icon("minus", "sm")}</button><span>${i.quantity}</span><button data-inc aria-label="+">${icon("plus", "sm")}</button></div>
          <button class="icon-btn" data-del aria-label="${esc(t("remove"))}">${icon("trash")}</button>
        </div>`).join("")}
      </div>
      <div class="card">
        <div class="sum-row"><span class="muted">${esc(t("items"))}</span><b>${cart.count}</b></div>
        <div class="sum-row total"><span>${esc(t("total"))}</span><span>${money(cart.subtotal)}</span></div>
        ${!cart.valid ? notice(esc(t("cart_has_unavailable")), "err") : ""}
        ${tooMany ? notice(esc(t("max_10_per_line")), "") : ""}
        <p class="help">${icon("info", "sm")} ${esc(t("one_order_per_unit"))}</p>
        <button class="btn primary block" id="c-checkout" ${cart.valid && !tooMany ? "" : "disabled"} style="margin-top:12px">${icon("check")} ${esc(t("checkout"))}</button>
      </div>
    </div>`;

  const update = async (fn) => {
    try { draw(view, (await fn()).cart); refreshBadges(); } catch (e) { toast(errMsg(e), "err"); }
  };
  view.querySelectorAll("[data-item]").forEach((row) => {
    const id = row.dataset.item;
    const item = cart.items.find((x) => String(x.id) === id);
    row.querySelector("[data-inc]").addEventListener("click", () =>
      update(() => api(`/api/cart/items/${id}`, { method: "PATCH", body: { quantity: Math.min(10, item.quantity + 1) } })));
    row.querySelector("[data-dec]").addEventListener("click", () => {
      if (item.quantity <= 1) return update(() => api(`/api/cart/items/${id}`, { method: "DELETE" }));
      return update(() => api(`/api/cart/items/${id}`, { method: "PATCH", body: { quantity: item.quantity - 1 } }));
    });
    row.querySelector("[data-del]").addEventListener("click", () => update(() => api(`/api/cart/items/${id}`, { method: "DELETE" })));
  });
  view.querySelector("#c-clear").addEventListener("click", () => update(() => api("/api/cart/clear", { method: "POST" })));
  view.querySelector("#c-checkout").addEventListener("click", () => openPayModal({ cart }));
}
