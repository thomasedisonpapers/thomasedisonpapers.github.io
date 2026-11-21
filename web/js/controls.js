// The site's own form controls: a menu in place of the browser's select box, and a two-handle range over a histogram.
import { esc, icon } from "./core.js";

let openMenu = null;

function closeMenu() {
  if (!openMenu) return;
  openMenu.list.hidden = true;
  openMenu.button.setAttribute("aria-expanded", "false");
  openMenu = null;
}

if (typeof document !== "undefined") {
  document.addEventListener("click", (event) => { if (openMenu && !event.target.closest(".menu")) closeMenu(); });
  window.addEventListener("resize", closeMenu);
  document.addEventListener("scroll", (event) => { if (openMenu && !openMenu.list.contains(event.target)) closeMenu(); }, true);
}

/** Replaces a <select> on screen with a styled menu; the select stays underneath, so forms and change events work unchanged. */
export function enhanceSelect(select, { label = "" } = {}) {
  if (!select || select.dataset.enhanced) return;
  select.dataset.enhanced = "1";
  select.classList.add("sr-only");
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");
  const wrap = document.createElement("div");
  wrap.className = "menu";
  const named = label || (select.labels && select.labels[0] && select.labels[0].textContent.trim()) || select.getAttribute("aria-label") || "";
  wrap.innerHTML = `<button type="button" class="menu-button" aria-haspopup="listbox" aria-expanded="false" aria-label="${esc(named)}">
      <span class="menu-value"></span>${icon("chevron-down")}</button><ul class="menu-list" role="listbox" hidden tabindex="-1" aria-label="${esc(named)}"></ul>`;
  select.after(wrap);
  const button = wrap.querySelector("button");
  const list = wrap.querySelector("ul");
  const options = () => [...select.options];
  const render = () => {
    const current = select.options[select.selectedIndex];
    wrap.querySelector(".menu-value").textContent = current ? current.textContent : "";
    button.setAttribute("aria-label", `${named}: ${current ? current.textContent : ""}`);
    list.innerHTML = options().map((option, n) => `<li role="option" id="${select.id || "m"}-opt-${n}" data-index="${n}"
      aria-selected="${n === select.selectedIndex}">${icon("badge-check", "tick")}<span>${esc(option.textContent)}</span></li>`).join("");
  };
  const place = () => {
    const anchor = button.getBoundingClientRect();
    list.style.minWidth = `${anchor.width}px`;
    const below = anchor.bottom + 6 + list.offsetHeight <= window.innerHeight - 8;
    list.style.top = `${below ? anchor.bottom + 6 : Math.max(8, anchor.top - 6 - list.offsetHeight)}px`;
    list.style.left = `${Math.max(8, Math.min(anchor.left, window.innerWidth - list.offsetWidth - 8))}px`;
  };
  let active = 0;
  const highlight = (n) => {
    active = Math.max(0, Math.min(options().length - 1, n));
    list.querySelectorAll("li").forEach((li, i) => li.classList.toggle("active", i === active));
    list.setAttribute("aria-activedescendant", `${select.id || "m"}-opt-${active}`);
    const li = list.children[active];
    if (li) li.scrollIntoView({ block: "nearest" });
  };
  const choose = (n) => {
    if (n !== select.selectedIndex) {
      select.selectedIndex = n;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      select.dispatchEvent(new Event("input", { bubbles: true }));
    }
    render();
    closeMenu();
    button.focus();
  };
  const open = () => {
    closeMenu();
    render();
    list.hidden = false;
    button.setAttribute("aria-expanded", "true");
    openMenu = { list, button };
    place();
    highlight(select.selectedIndex);
    list.focus();
  };
  button.addEventListener("click", () => (list.hidden ? open() : closeMenu()));
  button.addEventListener("keydown", (event) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) { event.preventDefault(); open(); }
  });
  list.addEventListener("click", (event) => {
    const li = event.target.closest("li[data-index]");
    if (li) choose(Number(li.dataset.index));
  });
  list.addEventListener("pointermove", (event) => {
    const li = event.target.closest("li[data-index]");
    if (li) highlight(Number(li.dataset.index));
  });
  let typed = "";
  let typedAt = 0;
  list.addEventListener("keydown", (event) => {
    const moves = { ArrowDown: active + 1, ArrowUp: active - 1, Home: 0, End: options().length - 1 };
    if (event.key in moves) { event.preventDefault(); highlight(moves[event.key]); return; }
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); choose(active); return; }
    if (event.key === "Escape" || event.key === "Tab") { closeMenu(); button.focus(); return; }
    if (event.key.length === 1) {
      // Typing jumps to the first option starting with what was typed, as a native select does.
      typed = Date.now() - typedAt > 700 ? event.key.toLowerCase() : typed + event.key.toLowerCase();
      typedAt = Date.now();
      const hit = options().findIndex((o) => o.textContent.trim().toLowerCase().startsWith(typed));
      if (hit >= 0) highlight(hit);
    }
  });
  select.addEventListener("change", render);
  render();
}

/**
 * Two handles over a chart: hidden until the chart is hovered or focused, then dragged or moved with the arrow keys.
 * A handle follows the pointer smoothly and settles on a whole step only when let go, when `onChange(from, to)` fires.
 */
export function rangeSlider(host, { min, max, from, to, step = 1, label = "Range", onChange }) {
  host.classList.add("range");
  host.innerHTML = `<div class="range-rail"><div class="range-fill"></div></div><div class="range-band"></div>
    <button type="button" class="range-thumb" data-end="from" role="slider" aria-label="${esc(label)} from"></button>
    <button type="button" class="range-thumb" data-end="to" role="slider" aria-label="${esc(label)} to"></button>
    <output class="range-readout" aria-hidden="true"></output>`;
  const span = Math.max(1, max - min);
  const value = { from: Math.max(min, from ?? min), to: Math.min(max, to ?? max) };
  const band = host.querySelector(".range-band");
  const fill = host.querySelector(".range-fill");
  const readout = host.querySelector(".range-readout");
  const thumbs = { from: host.querySelector('[data-end="from"]'), to: host.querySelector('[data-end="to"]') };
  const snap = (v) => Math.round(Math.max(min, Math.min(max, v)) / step) * step;
  const pct = (v) => ((v - min) / span) * 100;
  let frame = 0;
  const draw = () => {
    frame = 0;
    for (const end of ["from", "to"]) {
      thumbs[end].style.left = `${pct(value[end])}%`;
      thumbs[end].setAttribute("aria-valuemin", String(min));
      thumbs[end].setAttribute("aria-valuemax", String(max));
      thumbs[end].setAttribute("aria-valuenow", String(snap(value[end])));
    }
    for (const el of [band, fill]) {
      el.style.left = `${pct(value.from)}%`;
      el.style.width = `${pct(value.to) - pct(value.from)}%`;
    }
    const [a, b] = [snap(value.from), snap(value.to)];
    readout.textContent = a === b ? `${a}` : `${a}–${b}`;
    readout.style.left = `${(pct(value.from) + pct(value.to)) / 2}%`;
    host.classList.toggle("narrowed", a > min || b < max);
  };
  // One paint per frame however fast the pointer moves, so dragging stays smooth.
  const schedule = () => { if (!frame) frame = requestAnimationFrame(draw); };
  const place = (end, v, settle) => {
    const bounded = Math.max(min, Math.min(max, v));
    const next = settle ? snap(bounded) : bounded;
    value[end] = end === "from" ? Math.min(next, value.to) : Math.max(next, value.from);
    schedule();
  };
  const commit = () => {
    value.from = snap(value.from);
    value.to = snap(value.to);
    draw();
    if (onChange) onChange(value.from > min ? value.from : null, value.to < max ? value.to : null);
  };
  const at = (event) => {
    const box = host.getBoundingClientRect();
    return min + ((event.clientX - box.left) / box.width) * span;
  };
  for (const end of ["from", "to"]) {
    const thumb = thumbs[end];
    thumb.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      thumb.setPointerCapture(event.pointerId);
      host.classList.add("dragging");
      thumb.focus();
    });
    thumb.addEventListener("pointermove", (event) => {
      if (thumb.hasPointerCapture(event.pointerId)) place(end, at(event), false);
    });
    const release = (event) => {
      if (!thumb.hasPointerCapture(event.pointerId)) return;
      thumb.releasePointerCapture(event.pointerId);
      host.classList.remove("dragging");
      commit();
    };
    thumb.addEventListener("pointerup", release);
    thumb.addEventListener("pointercancel", release);
    thumb.addEventListener("keydown", (event) => {
      const moves = { ArrowLeft: -step, ArrowDown: -step, ArrowRight: step, ArrowUp: step, PageDown: -10 * step, PageUp: 10 * step };
      if (event.key in moves) { event.preventDefault(); place(end, snap(value[end]) + moves[event.key], true); commit(); }
      if (event.key === "Home") { event.preventDefault(); place(end, end === "from" ? min : value.from, true); commit(); }
      if (event.key === "End") { event.preventDefault(); place(end, end === "to" ? max : value.to, true); commit(); }
    });
  }
  // A click on the chart moves whichever handle is nearer, so a range can be set without finding a handle first.
  host.addEventListener("click", (event) => {
    if (event.target.closest(".range-thumb")) return;
    const v = at(event);
    place(Math.abs(v - value.from) <= Math.abs(v - value.to) ? "from" : "to", v, true);
    commit();
  });
  draw();
  return { set: (f, t) => { value.from = f ?? min; value.to = t ?? max; draw(); } };
}
