/**
 * Системное объявление (техработы): /api/banner
 */
(function () {
  const host = document.getElementById("site-banner-slot");
  if (!host) return;

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  async function render() {
    try {
      const r = await fetch("/api/banner", { credentials: "include" });
      if (!r.ok) return;
      const b = await r.json();
      host.innerHTML = "";
      if (!b || !b.active) return;
      const msg = (b.message && String(b.message).trim()) || "";
      const sched = (b.scheduleText && String(b.scheduleText).trim()) || "";
      if (!msg && !sched) return;
      const bar = document.createElement("div");
      bar.className = "site-banner";
      bar.setAttribute("role", "status");
      if (msg) bar.innerHTML = "<p class=\"site-banner__msg\">" + esc(msg) + "</p>";
      if (sched) {
        const s = document.createElement("p");
        s.className = "site-banner__schedule";
        s.innerHTML = "<strong>Время работ:</strong> " + esc(sched);
        bar.appendChild(s);
      }
      host.appendChild(bar);
    } catch {
      /* ignore */
    }
  }

  window.refreshSiteBanner = render;
  render();
})();
