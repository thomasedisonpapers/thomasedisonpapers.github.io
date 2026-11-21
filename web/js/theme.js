// Applies a saved light or dark choice before the page paints, so it never flashes the other theme.
try {
  const saved = localStorage.getItem("archive-theme");
  if (saved === "dark" || saved === "light") document.documentElement.dataset.theme = saved;
} catch (error) {
  // Storage can be blocked (a private window); the system preference then decides.
}
