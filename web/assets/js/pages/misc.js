/* Account marketplace (moderated) + Support page. */
import { api, state, esc, safeUrl, money, t, icon, toast, modal, loading, empty, notice, pageHead, requireLogin, errMsg, withBusy, statusPill, dateTime, getLang, confirmDialog, FALLBACK_IMG } from "../core.js";
import { tgLink } from "../shell.js";

/* ---------------- MARKET ---------------- */
export async function renderMarket(view, params) {
  const tab = params.tab || "all";
  view.innerHTML = `${pageHead(t("nav_market"), { extra: `<button class="btn sm primary" id="m-new">${icon("plus", "sm")} ${esc(t("sell_account"))}</button>` })}
    ${notice(esc(t("market_intro")), "info", "shield")}
    <div class="tabs" style="margin-top:14px;max-width:420px"><button class="${tab === "all" ? "active" : ""}" data-t="all">${esc(t("listings"))}</button><button class="${tab === "mine" ? "active" : ""}" data-t="mine">${esc(t("my_listings"))}</button></div>
    <div id="m-list">${loading()}</div>`;
  view.querySelectorAll("[data-t]").forEach((b) => b.addEventListener("click", () => { location.hash = `#/market${b.dataset.t === "mine" ? "?tab=mine" : ""}`; }));
  view.querySelector("#m-new").addEventListener("click", () => { if (requireLogin()) newListing(view); });
  const list = view.querySelector("#m-list");
  try {
    if (tab === "mine") {
      if (!requireLogin()) return;
      const r = await api("/api/market/mine");
      list.innerHTML = r.items.length ? `<div class="card list">${r.items.map((l) => `<div class="li">
        <img class="thumb-sm" src="${safeUrl(l.game && l.game.icon_url, FALLBACK_IMG)}" alt="">
        <div class="grow"><b>${esc(l.title)}</b><div class="tiny muted">${esc(l.game ? l.game.name : "")} · ${dateTime(l.created_at)}</div>
        ${l.moderation_note ? `<div class="tiny muted">${esc(l.moderation_note)}</div>` : ""}</div>
        <div style="text-align:right"><b>${money(l.price)}</b><div>${statusPill(l.status)}</div></div>
        ${l.status !== "SOLD" ? `<button class="icon-btn" data-del="${l.id}" aria-label="${esc(t("remove"))}">${icon("trash")}</button>` : ""}</div>`).join("")}</div>`
        : `<div class="card">${empty("store", t("no_listings"))}</div>`;
      list.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
        if (!(await confirmDialog(t("delete_q"), { danger: true }))) return;
        try { await api(`/api/market/${b.dataset.del}`, { method: "DELETE" }); renderMarket(view, params); } catch (e) { toast(errMsg(e), "err"); }
      }));
    } else {
      const r = await api("/api/market?page_size=60");
      const support = ((state.config || {}).branding || {}).support_telegram;
      list.innerHTML = r.items.length ? `<div class="grid g3">${r.items.map((l) => `<div class="card stack">
        <div class="row"><img class="thumb-sm" src="${safeUrl(l.game && l.game.icon_url, FALLBACK_IMG)}" alt="" style="width:48px;height:48px;border-radius:12px">
        <div class="grow"><b class="ellipsis" style="display:block">${esc(l.title)}</b><div class="tiny muted">${esc(l.game ? l.game.name : "")} · ${esc(l.seller)}</div></div></div>
        ${l.description ? `<p class="small muted" style="margin:0;white-space:pre-line">${esc(l.description.slice(0, 280))}</p>` : ""}
        <div class="row between"><b style="font-size:18px">${money(l.price)}</b>
        ${support ? `<a class="btn sm primary" target="_blank" rel="noopener" href="${safeUrl(tgLink(support))}">${icon("send", "sm")} ${esc(t("contact_to_buy"))}</a>` : `<a class="btn sm" href="#/support">${esc(t("contact_to_buy"))}</a>`}</div>
        <div class="tiny muted">ID #${l.id}</div></div>`).join("")}</div>`
        : `<div class="card">${empty("store", t("no_listings"), t("no_listings_d"))}</div>`;
    }
  } catch (e) { list.innerHTML = notice(esc(errMsg(e)), "err"); }
}

async function newListing(view) {
  const games = await api("/api/games?page_size=100").catch(() => ({ items: [] }));
  const m = modal(t("sell_account"), `<form class="stack">
    <label class="field"><span>${esc(t("game"))}</span><select class="input" name="game_slug" required>${games.items.map((g) => `<option value="${esc(g.slug)}">${esc(g.name)}</option>`).join("")}</select></label>
    <label class="field"><span>${esc(t("title"))}</span><input class="input" name="title" required minlength="4" maxlength="120"></label>
    <label class="field"><span>${esc(t("description"))}</span><textarea class="input" name="description" maxlength="2000" placeholder="${esc(t("listing_desc_ph"))}"></textarea></label>
    <label class="field"><span>${esc(t("price_sum"))}</span><input class="input" name="price" type="number" min="1000" required inputmode="numeric"></label>
    ${notice(esc(t("never_share_password")), "", "lock")}
    <button class="btn primary block">${esc(t("submit_for_review"))}</button></form>`);
  const f = m.el.querySelector("form");
  f.addEventListener("submit", (e) => {
    e.preventDefault();
    withBusy(f.querySelector("button"), async () => {
      try {
        await api("/api/market", { method: "POST", body: { game_slug: f.game_slug.value, title: f.title.value.trim(), description: f.description.value.trim(), price: Number(f.price.value) } });
        m.close(); toast(t("listing_submitted"));
        location.hash = "#/market?tab=mine";
      } catch (ex) { toast(errMsg(ex), "err"); }
    });
  });
}

/* ---------------- SUPPORT ---------------- */
export async function renderSupport(view) {
  const b = (state.config || {}).branding || {};
  view.innerHTML = pageHead(t("nav_support")) + loading();
  const faq = await api(`/api/support/faq?lang=${getLang()}`).catch(() => ({ items: [] }));
  const contacts = [
    b.support_telegram && ["telegram", "Telegram", tgLink(b.support_telegram), b.support_telegram],
    b.telegram_channel_url && ["send", t("channel"), b.telegram_channel_url, b.telegram_channel_url.replace(/^https?:\/\//, "")],
    b.support_email && ["file", "Email", `mailto:${b.support_email}`, b.support_email],
    b.support_phone && ["headset", t("phone"), `tel:${b.support_phone.replace(/[^+\d]/g, "")}`, b.support_phone],
    b.instagram_url && ["image", "Instagram", b.instagram_url, b.instagram_url.replace(/^https?:\/\//, "")],
  ].filter(Boolean);
  view.innerHTML = `${pageHead(t("nav_support"))}
    <div class="grid g2">
      <div class="stack">
        <div class="card stack"><h3>${esc(t("contact_us"))}</h3>
          ${contacts.length ? `<div class="menu-list">${contacts.map(([ic, label, href, text]) => {
            const safe = /^(https?:|mailto:|tel:)/.test(href) ? esc(href) : "#";
            return `<a href="${safe}" target="_blank" rel="noopener"><span class="ic">${icon(ic)}</span><span class="grow"><b>${esc(label)}</b><div class="small muted">${esc(text)}</div></span>${icon("right", "sm")}</a>`;
          }).join("")}</div>` : `<p class="muted small">${esc(t("support_not_configured"))}</p>`}
          <div class="row small muted">${icon("clock", "sm")} ${esc(t("working_hours"))}: ${esc(b.support_hours || "—")}</div>
        </div>
        ${notice(esc(t("support_tip")), "info", "info")}
      </div>
      <div class="stack"><h3 style="margin:4px 4px 0">${esc(t("faq"))}</h3>
        ${(faq.items || []).map((f) => `<details class="card faq"><summary>${esc(f.question)} ${icon("down", "sm")}</summary><p class="muted" style="margin:10px 0 0">${esc(f.answer)}</p></details>`).join("")}
      </div>
    </div>`;
}
