// Kosten-App: nur Komfort, alles geht auch ohne JavaScript.
// - „Kopieren“ beim neuen Key (der Klartext steht nur auf dieser einen Seite)
// - Formulare nicht doppelt absenden (Knopf nach dem ersten Tippen sperren)
"use strict";
document.addEventListener("click", async (ereignis) => {
  const knopf = ereignis.target.closest("[data-kopieren]");
  if (!knopf) return;
  const feld = document.getElementById(knopf.dataset.kopieren);
  if (!feld) return;
  try {
    await navigator.clipboard.writeText(feld.value);
  } catch (_) {
    feld.focus();
    feld.select();
    feld.setSelectionRange(0, feld.value.length);
    document.execCommand("copy");
  }
  knopf.textContent = "Kopiert ✓";
});
document.addEventListener("submit", (ereignis) => {
  const knopf = ereignis.target.querySelector("button[type=submit]");
  if (knopf) setTimeout(() => { knopf.disabled = true; }, 0);
});
