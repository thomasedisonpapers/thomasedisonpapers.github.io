// Applies the reader's saved light or dark choice, or else the system's, before the page paints, so it never flashes the other theme.
(() => {
  let saved = null;
  try {
    saved = localStorage.getItem("archive-theme");
  } catch (error) {
    // Storage can be blocked (a private window); the system preference then decides.
  }
  if (saved === "dark" || saved === "light") {
    document.documentElement.dataset.theme = saved;
    return;
  }
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  const follow = () => {
    // A choice made with the theme button on this page outranks a later change of the system setting.
    if (document.documentElement.dataset.themeOverride) return;
    let chosen = null;
    try { chosen = localStorage.getItem("archive-theme"); } catch (error) { /* blocked storage: keep following the system */ }
    if (chosen !== "dark" && chosen !== "light") document.documentElement.dataset.theme = system.matches ? "dark" : "light";
  };
  follow();
  system.addEventListener("change", follow);
})();
