/* Wallet: balance, Hamyon card top-up (HUMO / UZCARD), history. */
import { api, state, esc, money, som, t, icon, toast, loading, notice, pageHead, requireLogin, errMsg, withBusy, dateTime, statusPill, copyText, confirmDialog, ApiError } from "../core.js";
import { updateBadges } from "../shell.js";

let timer = null;
function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }

export async function renderWallet(view, params) {
  stopTimer();
  if (!requireLogin()) return;
  view.innerHTML = pageHead(t("nav_wallet")) + loading();
  let w;
  try { w = await api("/api/wallet"); } catch (e) { view.innerHTML = pageHead(t("nav_wallet")) + notice(esc(errMsg(e)), "err"); return; }
  if (state.user) { state.user.balance = w.balance; updateBadges(); }
  const need = Math.max(Number(params.need || 0), 0);
  view.innerHTML = `${pageHead(t("nav_wallet"))}
    <div class="grid g2">
      <div class="stack">
        <div class="card balance-card">
          <div class="row between"><span class="muted">${esc(t("your_balance"))}</span>${icon("wallet")}</div>
          <div class="amount" style="margin:8px 0 4px">${money(w.balance)}</div>
          <div class="small muted">${esc(t("balance_hint"))}</div>
        </div>
        <div class="card" id="topup-box"></div>
      </div>
      <div class="card">
        <div class="tabs"><button class="active" data-h="tx">${esc(t("history"))}</button><button data-h="tp">${esc(t("topups"))}</button></div>
        <div id="hist">${loading()}</div>
      </div>
    </div>`;

  const box = view.querySelector("#topup-box");
  if (w.active_topup) drawPending(box, w.active_topup, view);
  else drawForm(box, w, need, view);

  const hist = view.querySelector("#hist");
  const loadHist = async (kind) => {
    hist.innerHTML = loading();
    try {
      if (kind === "tx") {
        const r = await api("/api/wallet/transactions?page_size=30");
        hist.innerHTML = r.items.length ? `<div class="list">${r.items.map((x) => `<div class="li">
          <div class="tx-ic" style="color:${x.amount >= 0 ? "var(--ok)" : "var(--muted)"}">${icon(x.amount >= 0 ? "plus" : "minus")}</div>
          <div class="grow"><b>${esc(t("tx_" + x.kind))}</b><div class="tiny muted">${dateTime(x.created_at)}${x.reference ? " · " + esc(x.reference) : ""}</div></div>
          <b style="color:${x.amount >= 0 ? "var(--ok)" : "var(--text)"}">${x.amount >= 0 ? "+" : "−"}${money(Math.abs(x.amount))}</b></div>`).join("")}</div>`
          : `<p class="muted center" style="padding:30px 0">${esc(t("no_transactions"))}</p>`;
      } else {
        const r = await api("/api/wallet/topups?page_size=30");
        hist.innerHTML = r.items.length ? `<div class="list">${r.items.map((x) => `<div class="li">
          <div class="grow"><b>${som(x.pay_amount)}</b><div class="tiny muted">${dateTime(x.created_at)} · ${esc(x.reference)}</div></div>${statusPill(x.status)}</div>`).join("")}</div>`
          : `<p class="muted center" style="padding:30px 0">${esc(t("no_topups"))}</p>`;
      }
    } catch (e) { hist.innerHTML = notice(esc(errMsg(e)), "err"); }
  };
  view.querySelectorAll("[data-h]").forEach((b) => b.addEventListener("click", () => {
    view.querySelectorAll("[data-h]").forEach((x) => x.classList.toggle("active", x === b));
    loadHist(b.dataset.h);
  }));
  loadHist("tx");
}

function drawForm(box, w, need, view) {
  if (!w.topup_enabled) {
    box.innerHTML = `<h3>${esc(t("top_up_balance"))}</h3>${notice(`<b>${esc(t("topup_not_configured"))}</b><div class="small muted">${esc(t("topup_not_configured_d"))}</div>`, "info", "info")}`;
    return;
  }
  const initial = need ? Math.min(Math.max(need, w.min_amount), w.max_amount) : "";
  box.innerHTML = `<h3>${esc(t("top_up_balance"))}</h3>
    <p class="small muted">${esc(t("topup_intro"))}</p>
    <div class="presets" style="margin:12px 0">${w.presets.map((p) => `<button class="chip" data-preset="${p}">${som(p)}</button>`).join("")}</div>
    <form id="tp-form" class="stack">
      <label class="field"><span>${esc(t("amount_sum"))}</span>
        <input class="input" name="amount" type="number" inputmode="numeric" min="${w.min_amount}" max="${w.max_amount}" step="1" required value="${initial}" placeholder="${w.min_amount}"></label>
      <p class="help">${esc(t("topup_limits", { min: som(w.min_amount), max: som(w.max_amount) }))}</p>
      <button class="btn primary block">${icon("card")} ${esc(t("continue"))}</button>
    </form>
    <div class="row" style="margin-top:12px;gap:8px">${icon("shield", "sm")}<span class="tiny muted">${esc(t("topup_secure"))}</span></div>`;
  const form = box.querySelector("#tp-form");
  box.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => { form.amount.value = b.dataset.preset; }));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const btn = form.querySelector("button");
    withBusy(btn, async () => {
      try {
        const r = await api("/api/wallet/topups", { method: "POST", body: { amount: Number(form.amount.value) } });
        drawPending(box, r.topup, view);
      } catch (err) {
        if (err instanceof ApiError && err.detail && err.detail.code === "topup_amount_range")
          toast(t("topup_limits", { min: som(err.detail.min), max: som(err.detail.max) }), "err");
        else toast(errMsg(err), "err");
      }
    });
  });
}

function drawPending(box, topup, view) {
  stopTimer();
  const card = topup.card_number || "";
  box.innerHTML = `<div class="row between"><h3 style="margin:0">${esc(t("pay_by_card"))}</h3>${statusPill(topup.status)}</div>
    <div class="steps"><span class="on"></span><span class="on"></span><span></span></div>
    <p class="small muted">${esc(t("transfer_exact"))}</p>
    <div class="inset center" style="padding:14px">
      <div class="small muted">${esc(t("amount_to_pay"))}</div>
      <div style="font-size:28px;font-weight:800">${som(topup.pay_amount)}</div>
      ${topup.pay_amount !== topup.requested_amount ? `<div class="tiny muted">${esc(t("amount_adjusted"))}</div>` : ""}
    </div>
    <div class="inset pay-card-num mono" style="margin-top:12px">${esc(card.replace(/(\d{4})(?=\d)/g, "$1 "))}</div>
    <div class="row" style="margin-top:10px">
      <button class="btn grow" data-copy-card>${icon("copy")} ${esc(t("copy_card"))}</button>
      <button class="btn grow" data-copy-amt>${icon("copy")} ${esc(t("copy_amount"))}</button>
    </div>
    <div class="row between" style="margin-top:14px"><span class="muted">${icon("clock", "sm")} ${esc(t("time_left"))}</span><span class="timer" id="tp-timer">--:--</span></div>
    <div class="row" style="margin-top:8px"><div class="spinner" style="width:16px;height:16px;border-width:2px"></div><span class="small muted">${esc(t("waiting_payment"))}</span></div>
    <button class="btn ghost danger block" data-cancel style="margin-top:10px">${esc(t("cancel_topup"))}</button>`;
  box.querySelector("[data-copy-card]").addEventListener("click", () => copyText(card.replace(/\s/g, "")));
  box.querySelector("[data-copy-amt]").addEventListener("click", () => copyText(String(topup.pay_amount)));
  box.querySelector("[data-cancel]").addEventListener("click", async () => {
    if (!(await confirmDialog(t("cancel_topup_q"), { danger: true }))) return;
    try { const r = await api(`/api/wallet/topups/${encodeURIComponent(topup.reference)}/cancel`, { method: "POST" }); finish(r.topup); }
    catch (e) { toast(errMsg(e), "err"); }
  });
  let left = topup.seconds_left || 0;
  const tEl = box.querySelector("#tp-timer");
  let tick = 0;
  const finish = (tp) => {
    stopTimer();
    if (tp.status === "PAID") toast(t("topup_success"));
    else toast(t("topup_ended"), "info");
    renderWallet(view, {});
  };
  const render = () => {
    const m = Math.floor(left / 60), s = left % 60;
    tEl.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  };
  render();
  timer = setInterval(async () => {
    if (!document.body.contains(box)) { stopTimer(); return; }
    left = Math.max(0, left - 1);
    render();
    tick++;
    if (tick % 4 === 0 || left === 0) {
      try {
        const r = await api(`/api/wallet/topups/${encodeURIComponent(topup.reference)}`);
        if (r.topup.status !== "PENDING") finish(r.topup);
        else if (typeof r.topup.seconds_left === "number") left = r.topup.seconds_left;
      } catch (e) { /* keep polling */ }
    }
  }, 1000);
}
