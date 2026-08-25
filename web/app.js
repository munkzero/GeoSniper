/* GeoSniper frontend. Talks to the local Python backend, which proxies
   live New Zealand Petroleum & Minerals (NZP&M) ArcGIS data. */

const NZ_CENTER = [-41.0, 172.8];

// Quick-jump goldfields. Search covers ALL of NZ; these are just shortcuts.
const PRESETS = [
  { name: "Waihi", ll: [-37.39, 175.84], z: 13 },
  { name: "Coromandel", ll: [-36.76, 175.50], z: 12 },
  { name: "Reefton", ll: [-42.12, 171.86], z: 12 },
  { name: "Lawrence", ll: [-45.91, 169.69], z: 12 },
  { name: "Macraes", ll: [-45.40, 170.42], z: 12 },
  { name: "Golden Bay", ll: [-40.70, 172.80], z: 11 },
  { name: "Otago", ll: [-45.30, 169.30], z: 9 },
  { name: "West Coast", ll: [-42.70, 171.20], z: 8 },
  { name: "All NZ", ll: NZ_CENTER, z: 6 },
];

const map = L.map("map", { zoomControl: true }).setView(NZ_CENTER, 6);
L.tileLayer(
  "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
  { attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 19 }
).addTo(map);

const layers = {
  snipe: L.layerGroup().addTo(map),
  avail: L.layerGroup(),
  expired: L.layerGroup(),
  permits: L.layerGroup().addTo(map),
  apps: L.layerGroup(),
  blocks: L.layerGroup(),
  reports: L.layerGroup(),
};

const $ = (id) => document.getElementById(id);
const loader = $("loader");
let scanTimer = null;

function bbox() {
  const b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map((n) => n.toFixed(5)).join(",");
}
function commodity() { return $("commodity").value; }
function permitType() { return $("permitType").value; }
function expiringDays() { return parseInt($("expiring").value, 10) * 30; }

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error((await r.json()).error || r.statusText);
  return r.json();
}

function fmtDays(d) {
  if (d === null || d === undefined) return "no expiry date";
  if (d < 0) return `expired ${Math.abs(d)} days ago`;
  const m = Math.round(d / 30);
  return m < 24 ? `${m} mo left` : `${(d / 365).toFixed(1)} yr left`;
}

/* ---------- place search (nationwide) ---------- */
async function doSearch() {
  const q = $("search").value.trim();
  if (!q) return;
  const box = $("searchResults");
  box.innerHTML = "<div class='r'>searching…</div>";
  try {
    const { results } = await getJSON("/api/geocode?q=" + encodeURIComponent(q));
    if (!results.length) { box.innerHTML = "<div class='r'>no matches</div>"; return; }
    box.innerHTML = "";
    results.forEach((r) => {
      const el = document.createElement("div");
      el.className = "r";
      el.textContent = r.name;
      el.onclick = () => {
        box.innerHTML = "";
        if (r.bbox) {
          map.fitBounds([[r.bbox[1], r.bbox[0]], [r.bbox[3], r.bbox[2]]]);
        } else {
          map.setView([r.lat, r.lon], 12);
        }
      };
      box.appendChild(el);
    });
  } catch (e) { box.innerHTML = `<div class='r'>error: ${e.message}</div>`; }
}

/* ---------- rendering ---------- */
const permitStyle = (f) => {
  const d = f.properties.days_to_expiry;
  let color = "#e0453e";
  if (d !== null && d !== undefined) {
    if (d < 0) color = "#8794a3";
    else if (d <= expiringDays()) color = "#f5a623";
  }
  return { color, weight: 1, fillOpacity: 0.12 };
};

function permitPopup(p) {
  return `<b>Permit ${p.permit ?? "?"}</b> — ${p.mineral ?? ""}<br>` +
    `${p.type ?? ""}<br>` +
    `Operator: ${p.operator ?? "?"}<br>` +
    `Location: ${p.location ?? "?"}<br>` +
    `Area: ${p.area_km2 ?? "?"} km²<br>` +
    `Expiry: <b>${p.expiry ?? "?"}</b> (${fmtDays(p.days_to_expiry)})`;
}

const oppList = $("oppList");

function renderSnipe(fc) {
  layers.snipe.clearLayers();
  oppList.innerHTML = "";
  const feats = fc.features
    .sort((a, b) => rank(a.properties.status) - rank(b.properties.status));
  const colors = { open: "#2ecc71", expiring: "#f5a623", covered: "#7f8c9a" };

  feats.forEach((f) => {
    const p = f.properties;
    const [lon, lat] = f.geometry.coordinates;
    L.circleMarker([lat, lon], {
      radius: p.status === "covered" ? 3 : 5,
      color: colors[p.status], weight: 1,
      fillColor: colors[p.status],
      fillOpacity: p.status === "covered" ? 0.4 : 0.9,
    }).bindPopup(snipePopup(p)).addTo(layers.snipe);
  });

  // opportunity list: OPEN + EXPIRING first, grouped by prospect field.
  const targets = feats.filter((f) => f.properties.status !== "covered");
  $("oppCount").textContent = targets.length ? `(${targets.length})` : "";
  if (!targets.length) {
    oppList.innerHTML = "<p class='hint'>No open or expiring historic ground " +
      "in view. Try a wider expiry window, another commodity, or pan the map.</p>";
    return;
  }
  targets.slice(0, 300).forEach((f) => {
    const p = f.properties;
    const [lon, lat] = f.geometry.coordinates;
    const el = document.createElement("div");
    el.className = "opp " + p.status;
    el.innerHTML =
      `<div class='t'>${p.field || p.title || "drill hole"} ` +
      `<span class='badge ${p.status}'>${p.status}</span></div>` +
      `<div class='m'>${p.title || ""} · ${p.result || "result n/a"}` +
      (p.depth_m ? ` · ${p.depth_m} m` : "") + "</div>" +
      `<div class='m'>` +
      (p.status === "open"
        ? "No active permit over this historic drilling."
        : `Under permit ${p.covering_permit} — ` +
          `${fmtDays(p.covering_days_left)} (exp ${p.covering_expiry})`) +
      `</div>`;
    el.onclick = () => { map.setView([lat, lon], 15); };
    oppList.appendChild(el);
  });
}
const rank = (s) => ({ open: 0, expiring: 1, covered: 2 }[s] ?? 3);

function snipePopup(p) {
  let s = `<b>${p.field || p.title}</b> — <b>${p.status.toUpperCase()}</b><br>` +
    `Hole: ${p.title ?? "?"}<br>` +
    `Result: ${p.result ?? "?"}${p.depth_m ? " · " + p.depth_m + " m" : ""}<br>` +
    `Historic operator: ${p.hist_operator ?? "?"}<br>` +
    `Historic permit: ${p.hist_permit ?? "?"}<br>`;
  if (p.status === "open") {
    s += "✅ <b>No active permit here</b> — ground appears available.";
  } else {
    s += `Covered by permit <b>${p.covering_permit}</b><br>` +
      `Expiry: ${p.covering_expiry} (${fmtDays(p.covering_days_left)})<br>` +
      `Operator: ${p.covering_operator ?? "?"}`;
  }
  return s;
}

function expiredPopup(p) {
  const of = String(p.open_file || "").toLowerCase() === "yes"
    ? "<br>📄 <b>Open-file</b> — <a href='https://data.nzpam.govt.nz/' " +
      "target='_blank' rel='noopener'>NZP&amp;M data catalogue ↗</a>"
    : "";
  return `<b>⛏️ Relinquished — permit ${p.permit ?? "?"}</b><br>` +
    `${p.title ?? ""}<br>` +
    `Commodity: ${p.commodity ?? "?"} · ${p.region ?? ""}<br>` +
    `Operator: ${p.operator ?? "?"}<br>` +
    `Given up around: <b>${p.end_date ?? "?"}</b><br>` +
    `<div style='max-width:260px'>${(p.summary || "").slice(0, 300)}</div>` + of;
}

function reportPopup(p) {
  const of = String(p.open_file || "").toLowerCase() === "yes"
    ? "<br>📄 <b>Open-file</b> — searchable in the " +
      "<a href='https://data.nzpam.govt.nz/' target='_blank' rel='noopener'>" +
      "NZP&amp;M data catalogue ↗</a>"
    : "";
  return `<b>${p.title ?? "Report"}</b><br>` +
    `Commodity: ${p.commodity ?? "?"}<br>` +
    `Report ID: ${p.report_id ?? "?"} · Permit: ${p.permit ?? "?"}<br>` +
    `${p.region ?? ""} · ${p.start_date ?? "?"} – ${p.end_date ?? "?"}<br>` +
    `<div style='max-width:260px'>${(p.summary || "").slice(0, 300)}</div>` +
    of;
}

/* ---------- available (unclaimed) ground ---------- */
async function renderAvailable(bb) {
  layers.avail.clearLayers();
  if (map.getZoom() < 8) {
    L.popup().setLatLng(map.getCenter())
      .setContent("Zoom in a bit to compute available ground.").openOn(map);
    return;
  }
  // ANY active permit blocks ground, so pull them all (no commodity/type filter).
  const [permitsFc, appsFc] = await Promise.all([
    getJSON(`/api/permits?bbox=${bb}&mineral=all&permit_type=all`),
    getJSON(`/api/applications?bbox=${bb}&mineral=all&permit_type=all`),
  ]);
  const b = map.getBounds();
  let free = turf.bboxPolygon(
    [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
  // Subtract active permits AND pending applications (don't chase applied-for land).
  for (const f of [...permitsFc.features, ...appsFc.features]) {
    try {
      const diff = turf.difference(free, f);
      if (diff) free = diff; else { free = null; break; }
    } catch (_) { /* skip malformed geometry */ }
  }
  if (free) {
    L.geoJSON(free, {
      style: { color: "#1e7a45", weight: 1, fillColor: "#2ecc71",
        fillOpacity: 0.22, interactive: false },
    }).addTo(layers.avail);
  }
}

/* ---------- main scan ---------- */
async function scan() {
  const bb = bbox();
  const c = commodity();
  loader.hidden = false;
  try {
    const jobs = [];

    if ($("lyrSnipe").checked) {
      jobs.push(getJSON(
        `/api/snipe?bbox=${bb}&commodity=${c}&expiring_days=${expiringDays()}`
      ).then((fc) => {
        $("summaryPanel").hidden = false;
        $("cOpen").textContent = fc.summary.open;
        $("cExp").textContent = fc.summary.expiring;
        $("cCov").textContent = fc.summary.covered;
        $("scanHint").textContent =
          `${fc.active_permits_in_view} active permits in view · ` +
          `${fc.features.length} historic ${c} drill holes cross-referenced.`;
        renderSnipe(fc);
      }));
    } else {
      layers.snipe.clearLayers();
    }

    if ($("lyrAvail").checked) {
      jobs.push(renderAvailable(bb));
    } else { layers.avail.clearLayers(); }

    if ($("lyrPermits").checked) {
      jobs.push(getJSON(`/api/permits?bbox=${bb}&mineral=${c}&permit_type=${permitType()}`).then((fc) => {
        layers.permits.clearLayers();
        L.geoJSON(fc, { style: permitStyle,
          onEachFeature: (f, l) => l.bindPopup(permitPopup(f.properties)) })
          .addTo(layers.permits);
      }));
    } else { layers.permits.clearLayers(); }

    if ($("lyrApps").checked) {
      jobs.push(getJSON(`/api/applications?bbox=${bb}&mineral=${c}&permit_type=${permitType()}`).then((fc) => {
        layers.apps.clearLayers();
        L.geoJSON(fc, { style: { color: "#d67cff", weight: 1, dashArray: "4",
          fillOpacity: 0.08 },
          onEachFeature: (f, l) => l.bindPopup(
            `<b>Application ${f.properties.permit ?? ""}</b><br>` +
            `${f.properties.mineral ?? ""} · ${f.properties.type ?? ""}<br>` +
            `${f.properties.operator ?? ""}`) }).addTo(layers.apps);
      }));
    } else { layers.apps.clearLayers(); }

    if ($("lyrBlocks").checked) {
      jobs.push(getJSON(`/api/opportunities-blocks`).then((fc) => {
        layers.blocks.clearLayers();
        L.geoJSON(fc, { style: { color: "#23c2c2", weight: 2,
          fillOpacity: 0.1 },
          onEachFeature: (f, l) => l.bindPopup(
            `<b>${f.properties.kind === "open_contest"
              ? "Open contest round" : "Reserved area"}</b><br>` +
            "Ground NZP&amp;M is currently offering / holding.") })
          .addTo(layers.blocks);
      }));
    } else { layers.blocks.clearLayers(); }

    if ($("lyrExpired").checked) {
      jobs.push(getJSON(`/api/expired?bbox=${bb}&commodity=${c}`).then((fc) => {
        layers.expired.clearLayers();
        L.geoJSON(fc, { style: { color: "#b5651d", weight: 1.5,
          fillColor: "#e8873b", fillOpacity: 0.18, dashArray: "5,4" },
          onEachFeature: (f, l) => l.bindPopup(expiredPopup(f.properties)) })
          .addTo(layers.expired);
      }));
    } else { layers.expired.clearLayers(); }

    if ($("lyrReports").checked) {
      jobs.push(getJSON(`/api/reports?bbox=${bb}&commodity=${c}`).then((fc) => {
        layers.reports.clearLayers();
        L.geoJSON(fc, { style: { color: "#4a90e2", weight: 1,
          fillOpacity: 0.05 },
          onEachFeature: (f, l) => l.bindPopup(reportPopup(f.properties)) })
          .addTo(layers.reports);
      }));
    } else { layers.reports.clearLayers(); }

    await Promise.all(jobs);
  } catch (e) {
    $("scanHint").textContent = "Error: " + e.message;
    $("summaryPanel").hidden = false;
  } finally {
    loader.hidden = true;
  }
}

/* ---------- layer checkbox -> map ---------- */
function bindLayerToggle(id, group) {
  $(id).addEventListener("change", (e) => {
    if (e.target.checked) group.addTo(map); else map.removeLayer(group);
    scan();
  });
}
bindLayerToggle("lyrAvail", layers.avail);
bindLayerToggle("lyrExpired", layers.expired);
bindLayerToggle("lyrApps", layers.apps);
bindLayerToggle("lyrBlocks", layers.blocks);
bindLayerToggle("lyrReports", layers.reports);
$("lyrSnipe").addEventListener("change", scan);
$("lyrPermits").addEventListener("change", scan);

/* ---------- wiring ---------- */
$("searchBtn").onclick = doSearch;
$("search").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
$("loadBtn").onclick = scan;
$("commodity").addEventListener("change", scan);
$("permitType").addEventListener("change", scan);
$("expiring").addEventListener("input", () => {
  $("expLabel").textContent = $("expiring").value + " months";
});
$("expiring").addEventListener("change", scan);

const presetBox = $("presets");
PRESETS.forEach((p) => {
  const b = document.createElement("button");
  b.textContent = p.name;
  b.onclick = () => map.setView(p.ll, p.z);
  presetBox.appendChild(b);
});

map.on("moveend", () => {
  if (!$("autoload").checked) return;
  clearTimeout(scanTimer);
  scanTimer = setTimeout(scan, 500);
});

// initial scan once tiles settle.
map.whenReady(() => setTimeout(scan, 400));
