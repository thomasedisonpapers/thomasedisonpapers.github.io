// The side-by-side reader shared by documents and account books: the scan viewer, its toolbar, the divider, focus mode and tabs.
import { datasetUrls, esc, icon } from "./core.js";

const MAX_ZOOM = 8;
const SPLIT_KEY = "archive-split";
const SPLIT_DEFAULT = 55;

// A toolbar button keeps its name for screen readers and on hover even when a narrow pane hides its label.
export function tool(act, iconName, title, label) {
  return `<button type="button" data-act="${act}" title="${esc(title)}" aria-label="${esc(title)}">${icon(iconName)}<span class="lbl">${esc(label)}</span></button>`;
}

/** The scan pane's markup; `unit` names what the arrows step through, a page of a document or a scan of a book. */
export function scanPane({ unit = "page", strip = false } = {}) {
  const Unit = unit[0].toUpperCase() + unit.slice(1);
  return `<section class="scan-pane" aria-label="Original scan">
      <div class="toolbar">
        ${tool("prev", "chevron-left", `Previous ${unit}`, "Previous")}<span class="page-label" aria-live="polite"></span>
        ${tool("next", "chevron-right", `Next ${unit}`, "Next")}<span class="spacer"></span>
        ${tool("out", "zoom-out", "Zoom out", "Zoom out")}${tool("in", "zoom-in", "Zoom in", "Zoom in")}<span class="sep" aria-hidden="true"></span>
        ${tool("fit", "maximize-2", `Fit the whole ${unit}`, `Fit ${unit}`)}${tool("width", "unfold-horizontal", `Fit the ${unit} width`, "Fit width")}
        ${tool("turn", "rotate-cw", "Rotate a quarter turn", "Rotate")}<span class="sep" aria-hidden="true"></span>
        ${tool("focus", "maximize", "Focus: fill the window with the scan and text", "Focus")}
      </div>
      <div class="viewer" role="img" tabindex="0" aria-roledescription="scan viewer" aria-label="${esc(Unit)}. Plus and minus zoom, arrow keys move, R rotates.">
        <img alt=""><div class="state" hidden></div></div>
      ${strip ? `<nav class="strip" aria-label="${esc(Unit)}s"></nav>` : ""}
    </section>
    <div class="splitter" role="separator" aria-orientation="vertical" tabindex="0" aria-valuemin="25" aria-valuemax="75"
      aria-label="Resize the scan and the transcription; use the arrow keys, or double-click to reset" title="Drag to resize"></div>`;
}

/** The Scan / Transcription switch phones use in place of the side-by-side layout. */
export function mobileSwitch() {
  return `<div class="segmented mobile-switch" role="radiogroup" aria-label="Show">
    <label><input type="radio" name="show" value="scan" checked><span>${icon("image")} Scan</span></label>
    <label><input type="radio" name="show" value="text"><span>${icon("file-text")} Transcription</span></label></div>`;
}

export function wireMobileSwitch(page) {
  page.querySelectorAll("input[name=show]").forEach((r) => r.addEventListener("change", () => { page.querySelector(".reader").dataset.show = r.value; }));
}

// The divider between scan and text: dragged, moved with the arrow keys, reset with a double-click, and remembered.
export function wireSplitter(reader, splitter) {
  const set = (percent) => {
    const value = Math.max(25, Math.min(75, Math.round(percent * 10) / 10));
    reader.style.setProperty("--split", `${value}%`);
    splitter.setAttribute("aria-valuenow", String(Math.round(value)));
    try { localStorage.setItem(SPLIT_KEY, String(value)); } catch { /* the split still works, it just is not remembered */ }
  };
  let saved = SPLIT_DEFAULT;
  try { saved = Number(localStorage.getItem(SPLIT_KEY)) || SPLIT_DEFAULT; } catch { /* storage can be blocked */ }
  set(saved);
  splitter.addEventListener("pointerdown", (event) => {
    splitter.setPointerCapture(event.pointerId);
    splitter.classList.add("dragging");
    document.body.classList.add("resizing");
  });
  splitter.addEventListener("pointermove", (event) => {
    if (!splitter.hasPointerCapture(event.pointerId)) return;
    const box = reader.getBoundingClientRect();
    set(((event.clientX - box.left) / box.width) * 100);
  });
  const stop = (event) => {
    if (splitter.hasPointerCapture(event.pointerId)) splitter.releasePointerCapture(event.pointerId);
    splitter.classList.remove("dragging");
    document.body.classList.remove("resizing");
  };
  splitter.addEventListener("pointerup", stop);
  splitter.addEventListener("pointercancel", stop);
  splitter.addEventListener("dblclick", () => set(SPLIT_DEFAULT));
  splitter.addEventListener("keydown", (event) => {
    const now = Number(splitter.getAttribute("aria-valuenow")) || SPLIT_DEFAULT;
    const moves = { ArrowLeft: now - 3, ArrowRight: now + 3, Home: SPLIT_DEFAULT };
    if (event.key in moves) { event.preventDefault(); set(moves[event.key]); }
  });
}

// Focus fills the window with the scan and the text; Escape or the same button brings the page back.
export function wireFocus(button) {
  const sync = () => {
    const on = document.body.classList.contains("focus-mode");
    button.innerHTML = `${icon(on ? "minimize" : "maximize")}<span class="lbl">${on ? "Exit focus" : "Focus"}</span>`;
    button.setAttribute("aria-pressed", String(on));
  };
  button.addEventListener("click", () => { document.body.classList.toggle("focus-mode"); sync(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && document.body.classList.contains("focus-mode")) { document.body.classList.remove("focus-mode"); sync(); }
  });
  sync();
}

export function wireTabs(page) {
  const tabs = [...page.querySelectorAll(".tabs [role=tab]")];
  tabs.forEach((tab) => tab.addEventListener("click", () => {
    tabs.forEach((t) => t.setAttribute("aria-selected", String(t === tab)));
    page.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== tab.dataset.tab; });
  }));
}

export class Viewer {
  /** `pages` are {n, view, full, thumb}; `onShow(index)` runs whenever another page or scan is shown. */
  constructor(pane, pages, { title = "", unit = "page", start = 0, onShow = null, onLoad = null } = {}) {
    this.pane = pane;
    this.pages = pages;
    this.title = title;
    this.unit = unit;
    this.onShow = onShow;
    this.onLoad = onLoad;
    this.el = pane.querySelector(".viewer");
    this.img = this.el.querySelector("img");
    this.stateEl = this.el.querySelector(".state");
    this.index = 0;
    this.turn = 0;
    this.request = 0;
    this.pointers = new Map();
    const act = (name, fn) => pane.querySelector(`[data-act=${name}]`).addEventListener("click", fn);
    act("prev", () => this.show(this.index - 1));
    act("next", () => this.show(this.index + 1));
    act("in", () => this.zoomCenter(1.25));
    act("out", () => this.zoomCenter(0.8));
    act("fit", () => { this.mode = "page"; this.fit(); });
    act("width", () => { this.mode = "width"; this.fit(); });
    act("turn", () => { this.turn = (this.turn + 90) % 360; this.fit(); });
    // Positions come from the viewer's own box, not the transformed image under the pointer.
    const point = (e) => { const r = this.el.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    this.el.addEventListener("wheel", (e) => { e.preventDefault(); this.zoomAt(...point(e), e.deltaY < 0 ? 1.15 : 1 / 1.15); }, { passive: false });
    this.el.addEventListener("dblclick", (e) => this.zoomAt(...point(e), 2));
    this.el.addEventListener("pointerdown", (e) => this.down(e));
    this.el.addEventListener("pointermove", (e) => this.move(e));
    ["pointerup", "pointercancel", "pointerleave"].forEach((t) => this.el.addEventListener(t, (e) => this.up(e)));
    this.el.addEventListener("keydown", (e) => this.key(e));
    this.mode = "page";
    // A chosen fit holds as the window or the divider changes size; a reader's own zoom is left alone.
    new ResizeObserver(() => { if (this.img.naturalWidth && this.mode !== "free") this.fit(); }).observe(this.el);
    this.buildStrip();
    this.show(start);
  }

  async buildStrip() {
    const strip = this.pane.querySelector(".strip");
    if (!strip) return;
    const Unit = this.unit[0].toUpperCase() + this.unit.slice(1);
    for (const page of this.pages) {
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("aria-label", `${Unit} ${page.n}`);
      const number = document.createElement("span");
      number.className = "strip-n";
      number.textContent = String(page.n);
      // A scan with no thumbnail, or one that fails to load, shows its number instead.
      if (page.thumb) {
        const img = document.createElement("img");
        img.alt = "";
        img.loading = "lazy";
        img.addEventListener("error", () => img.remove());
        img.src = (await datasetUrls(page.thumb))[0];
        button.append(img);
      }
      button.append(number);
      button.addEventListener("click", () => this.show(this.pages.indexOf(page)));
      strip.appendChild(button);
    }
    this.markStrip();
  }

  markStrip() {
    this.pane.querySelectorAll(".strip button").forEach((b, n) => {
      b.setAttribute("aria-current", String(n === this.index));
      // Only the strip scrolls to the current scan; the page itself stays where the reader left it.
      if (n === this.index) b.parentElement.scrollLeft = b.offsetLeft - (b.parentElement.clientWidth - b.clientWidth) / 2;
    });
  }

  // Each page tries its screen-size image on every source, then the full scan, before showing a Retry button.
  async show(n) {
    const Unit = this.unit[0].toUpperCase() + this.unit.slice(1);
    if (!this.pages.length) {
      this.message(`No scan is available for this ${this.unit === "scan" ? "book" : "document"}.`);
      return;
    }
    this.index = Math.max(0, Math.min(this.pages.length - 1, n));
    this.turn = 0;
    if (this.mode === "free") this.mode = "page";
    const page = this.pages[this.index];
    this.pane.querySelector(".page-label").textContent = `${Unit} ${page.n} of ${this.pages[this.pages.length - 1].n}`;
    this.pane.querySelector("[data-act=prev]").disabled = this.index === 0;
    this.pane.querySelector("[data-act=next]").disabled = this.index === this.pages.length - 1;
    this.markStrip();
    if (this.onShow) this.onShow(this.index);
    const mine = ++this.request;
    const candidates = [...(page.view ? await datasetUrls(page.view) : []), ...(page.full ? await datasetUrls(page.full) : [])];
    if (mine !== this.request) return;
    this.message("Loading the scan…");
    this.img.style.visibility = "hidden";
    for (const url of candidates) {
      if (mine !== this.request) return;
      const ok = await new Promise((resolve) => {
        const timer = setTimeout(() => resolve(false), 15000);
        this.img.onload = () => { clearTimeout(timer); resolve(true); };
        this.img.onerror = () => { clearTimeout(timer); resolve(false); };
        this.img.src = url;
      });
      if (mine !== this.request) return;
      if (ok) {
        this.img.alt = `${Unit} ${page.n}: ${this.title}`;
        this.el.setAttribute("aria-label", `${this.img.alt}. Plus and minus zoom, arrow keys move, R rotates.`);
        this.stateEl.hidden = true;
        this.img.style.visibility = "visible";
        this.fit();
        this.onLoad?.(url);
        return;
      }
    }
    this.message("This scan could not be loaded just now.", true);
  }

  message(text, retry = false) {
    this.stateEl.hidden = false;
    this.stateEl.innerHTML = `<div>${esc(text)}${retry ? `<br><button type="button" class="btn">${icon("rotate-cw")}Try again</button>` : ""}</div>`;
    if (retry) this.stateEl.querySelector("button").addEventListener("click", () => this.show(this.index));
  }

  dims() {
    const w = this.img.naturalWidth || 1;
    const h = this.img.naturalHeight || 1;
    return this.turn % 180 ? [h, w, w, h] : [w, h, w, h];
  }

  fit() {
    if (this.mode === "free") this.mode = "page";
    const [bw, bh, w, h] = this.dims();
    const vw = this.el.clientWidth;
    const vh = this.el.clientHeight;
    // The smallest zoom is always the whole page; "width" fills the pane's width and starts at the top of the page.
    this.fitScale = Math.min(vw / bw, vh / bh) * 0.96;
    this.scale = this.mode === "width" ? (vw / bw) * 0.96 : this.fitScale;
    this.tx = vw / 2 - (this.scale * w) / 2;
    this.ty = this.mode === "width" ? (this.scale * bh) / 2 - (this.scale * h) / 2 + vw * 0.02 : vh / 2 - (this.scale * h) / 2;
    this.apply();
  }

  apply() {
    const [, , w, h] = this.dims();
    // Turned about the page's centre, so fitting and zooming use the same centre whatever the turn.
    this.img.style.transform = `translate(${this.tx}px, ${this.ty}px) scale(${this.scale}) translate(${w / 2}px, ${h / 2}px) rotate(${this.turn}deg) translate(${-w / 2}px, ${-h / 2}px)`;
  }

  zoomAt(x, y, factor) {
    if (!this.fitScale) return;
    this.mode = "free";
    const next = Math.max(this.fitScale, Math.min(this.fitScale * MAX_ZOOM, this.scale * factor));
    const ratio = next / this.scale;
    this.tx = x - (x - this.tx) * ratio;
    this.ty = y - (y - this.ty) * ratio;
    this.scale = next;
    if (Math.abs(next - this.fitScale) < 1e-6) { this.fit(); return; }
    this.apply();
  }

  zoomCenter(factor) {
    this.zoomAt(this.el.clientWidth / 2, this.el.clientHeight / 2, factor);
  }

  down(e) {
    // A button inside the viewer (Try again) keeps its own click instead of starting a drag.
    if (e.target.closest("button")) return;
    this.el.setPointerCapture(e.pointerId);
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    this.el.classList.add("dragging");
  }

  move(e) {
    if (!this.pointers.has(e.pointerId)) return;
    this.mode = "free";
    const before = this.pointers.get(e.pointerId);
    if (this.pointers.size === 2) {
      // Two fingers: the change in their distance is the zoom, about their midpoint.
      const other = [...this.pointers.entries()].find(([id]) => id !== e.pointerId)[1];
      const rect = this.el.getBoundingClientRect();
      const was = Math.hypot(before.x - other.x, before.y - other.y);
      const now = Math.hypot(e.clientX - other.x, e.clientY - other.y);
      if (was > 0) this.zoomAt((e.clientX + other.x) / 2 - rect.left, (e.clientY + other.y) / 2 - rect.top, now / was);
    } else {
      this.tx += e.clientX - before.x;
      this.ty += e.clientY - before.y;
      this.apply();
    }
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  }

  pan(dx, dy) {
    this.mode = "free";
    this.tx += dx;
    this.ty += dy;
    this.apply();
  }

  up(e) {
    this.pointers.delete(e.pointerId);
    if (!this.pointers.size) this.el.classList.remove("dragging");
  }

  key(e) {
    const step = 60;
    const actions = {
      "+": () => this.zoomCenter(1.25), "=": () => this.zoomCenter(1.25), "-": () => this.zoomCenter(0.8), "0": () => this.fit(),
      ArrowLeft: () => this.pan(step, 0), ArrowRight: () => this.pan(-step, 0), ArrowUp: () => this.pan(0, step), ArrowDown: () => this.pan(0, -step),
      w: () => { this.mode = "width"; this.fit(); }, W: () => { this.mode = "width"; this.fit(); },
      r: () => { this.turn = (this.turn + 90) % 360; this.fit(); }, R: () => { this.turn = (this.turn + 90) % 360; this.fit(); },
      PageDown: () => this.show(this.index + 1), PageUp: () => this.show(this.index - 1),
    };
    if (actions[e.key]) { e.preventDefault(); actions[e.key](); }
  }
}
