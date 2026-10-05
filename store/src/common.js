// コピひろ — ストア画像用の共通部品（ブラウザ枠・ウィンドウボタン・カーソル）
// 各 HTML の末尾で読み込み、data-* 属性から汎用（ブランドなし）の UI を組み立てる。
(function () {
  "use strict";

  const svg = (w, h, body, extra) =>
    `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" ${extra || ""} xmlns="http://www.w3.org/2000/svg">${body}</svg>`;

  const ICON = {
    back: (c) => svg(18, 18, `<path d="M15 9H3.6M8.6 3.8 3.4 9l5.2 5.2" fill="none" stroke="${c}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>`),
    fwd: (c) => svg(18, 18, `<path d="M3 9h11.4M9.4 3.8 14.6 9l-5.2 5.2" fill="none" stroke="${c}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>`),
    reload: (c) => svg(18, 18, `<path d="M14.6 9.2A5.6 5.6 0 1 1 12.9 5" fill="none" stroke="${c}" stroke-width="1.7" stroke-linecap="round"/><path d="M13.6 2.2v3.6H10" fill="none" stroke="${c}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>`),
    tune: (c) => svg(16, 16, `<path d="M2.5 5h6M12 5h1.5M2.5 11h1.5M7.5 11h6" stroke="${c}" stroke-width="1.5" stroke-linecap="round"/><circle cx="10.2" cy="5" r="1.8" fill="none" stroke="${c}" stroke-width="1.5"/><circle cx="5.8" cy="11" r="1.8" fill="none" stroke="${c}" stroke-width="1.5"/>`),
    star: (c) => svg(18, 18, `<path d="m9 2.6 1.9 4 4.3.5-3.2 3 .9 4.3L9 12.2l-3.9 2.2.9-4.3-3.2-3 4.3-.5z" fill="none" stroke="${c}" stroke-width="1.4" stroke-linejoin="round"/>`),
    kebab: (c) => svg(18, 18, `<circle cx="9" cy="3.6" r="1.5" fill="${c}"/><circle cx="9" cy="9" r="1.5" fill="${c}"/><circle cx="9" cy="14.4" r="1.5" fill="${c}"/>`),
    close: (c, s) => svg(s || 10, s || 10, `<path d="M1 1l${(s || 10) - 2} ${(s || 10) - 2}M${(s || 10) - 1} 1 1 ${(s || 10) - 1}" stroke="${c}" stroke-width="1.1" stroke-linecap="round"/>`),
    plus: (c) => svg(14, 14, `<path d="M7 1.5v11M1.5 7h11" stroke="${c}" stroke-width="1.5" stroke-linecap="round"/>`),
    min: (c) => svg(10, 10, `<path d="M0.5 5h9" stroke="${c}" stroke-width="1"/>`),
    max: (c) => svg(10, 10, `<rect x="0.5" y="0.5" width="9" height="9" rx="1.5" fill="none" stroke="${c}" stroke-width="1"/>`),
    chev: (c) => svg(8, 12, `<path d="M2 1.5 6.5 6 2 10.5" fill="none" stroke="${c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>`),
    grid: (c) => svg(10, 10, `<path d="M0.5 3.5h9M0.5 6.5h9M3.5 0.5v9" stroke="${c}" stroke-width="1.2"/>`),
    doc: (c) => svg(12, 14, `<path d="M1.5 1.5h6l3 3v8h-9z" fill="none" stroke="${c}" stroke-width="1.2" stroke-linejoin="round"/><path d="M7.5 1.5v3h3" fill="none" stroke="${c}" stroke-width="1.2"/>`),
  };

  const winControls = (c) =>
    `<div class="winctl"><span>${ICON.min(c)}</span><span>${ICON.max(c)}</span><span>${ICON.close(c)}</span></div>`;

  // ブラウザ枠: <div class="win browser" data-title="…" data-host="…" data-path="…" data-fav="S">
  document.querySelectorAll(".win.browser").forEach((el) => {
    const title = el.dataset.title || "";
    const host = el.dataset.host || "example.com";
    const path = el.dataset.path || "";
    const fav = el.dataset.fav || "";
    const html =
      `<div class="tabstrip">` +
        `<div class="tab"><span class="fav">${fav}</span><span class="ttl">${title}</span><span class="x">${ICON.close("#555", 8)}</span></div>` +
        `<div class="newtab">${ICON.plus("#4a4a4a")}</div>` +
        winControls("#333") +
      `</div>` +
      `<div class="toolbar">` +
        `<span class="navbtn">${ICON.back("#474747")}</span>` +
        `<span class="navbtn dim">${ICON.fwd("#474747")}</span>` +
        `<span class="navbtn">${ICON.reload("#474747")}</span>` +
        `<div class="omnibox">${ICON.tune("#474747")}<span>${host}<span class="path">${path}</span></span>` +
          `<span style="margin-left:auto;display:flex">${ICON.star("#5f6368")}</span></div>` +
        `<span class="navbtn">${ICON.kebab("#474747")}</span>` +
      `</div>`;
    el.insertAdjacentHTML("afterbegin", html);
  });

  // 汎用アプリのタイトルバー: <div class="titlebar" data-title="…" data-icon="grid|doc" data-dark>
  document.querySelectorAll(".titlebar[data-title]").forEach((el) => {
    const dark = el.hasAttribute("data-dark");
    const c = dark ? "#c9d1de" : "#333";
    const icon = el.dataset.icon === "doc" ? ICON.doc("#fff") : ICON.grid("#fff");
    el.insertAdjacentHTML(
      "afterbegin",
      dark ? "" : `<span class="appico">${icon}</span><span>${el.dataset.title}</span>`
    );
    el.insertAdjacentHTML("beforeend", winControls(c));
  });

  // サブメニューの矢印
  document.querySelectorAll(".cmenu .chev").forEach((el) => {
    el.innerHTML = ICON.chev("#474747");
  });

  // マウスカーソル（標準の矢印ポインタ）: <i class="cursor" style="left:…;top:…"></i>
  document.querySelectorAll("i.cursor").forEach((el) => {
    el.outerHTML = `<span class="cursor" style="${el.getAttribute("style") || ""}">` +
      svg(22, 30, `<path d="M1.5 1.5v21.2l5.1-4.9 3.6 8.3 3.5-1.5-3.5-8.1h7.1z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/>`) +
      `</span>`;
  });
})();
