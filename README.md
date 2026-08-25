# 🎯 GeoSniper

**Find New Zealand mineral ground worth chasing** — unpermitted land, permits
about to expire, and historic gold/mineral drilling that no longer sits under
any active permit. Built on **live** [New Zealand Petroleum & Minerals
(NZP&M)](https://www.nzpam.govt.nz/) public GIS data.

> ⚠️ **Reality check.** GeoSniper is a research/scouting aid, not legal advice.
> Permit boundaries, availability and history must always be confirmed on the
> official [NZP&M webmaps](https://www.nzpam.govt.nz/maps-geoscience-data/) and
> through NZP&M before you spend a cent or lodge anything.

---

## What it does

- **Search anywhere in New Zealand** — type a place ("Lawrence", "Golden Bay",
  "Reefton") and the map flies there. Quick-jump presets for classic goldfields.
- **Snipe mode (the core):** in whatever area you're viewing, it cross-references
  ~30,000 historic mineral **drill holes** against every current active permit
  and tags each one:
  - 🟢 **OPEN** — historic drilling on ground with **no active permit** today.
  - 🟠 **EXPIRING** — under a permit that lapses within your chosen window
    (the ground is about to re-open).
  - ⚪ **COVERED** — under a permit with plenty of time left.
- **Layers you can toggle:**
  - Active mineral permits (colour-coded by time-to-expiry).
  - Pending applications.
  - Open contest rounds & reserved areas (ground NZP&M is actively offering).
  - Historic exploration **reports** — with summaries and open-file report links.
- **Filters:** commodity (Gold / Silver / Coal / Iron sands / All) and
  permit/claim type (Mining claims / Exploration / Prospecting / Fossicking).
- **Expiry window slider:** define how soon "expiring" means (3–120 months).

Everything auto-loads for the region you're looking at, so you never download
the whole country at once.

## The "historic gold, now expired" workflow

1. Search or pan to an old goldfield (e.g. Waihi → Golden Cross, or Otago).
2. Set commodity = **Gold** and widen the expiry window.
3. Watch for 🟢 **OPEN** and 🟠 **EXPIRING** drill holes — these are historic
   holes with recorded results that are (or are about to be) on free ground.
4. Turn on **Historic reports** and click a polygon to read the summary and open
   the original NZP&M open-file report for the assays/results.
5. Confirm on the official NZP&M webmaps before acting.

## Run it

No dependencies — just Python 3.9+ (standard library only).

```bash
python3 server.py
# then open http://localhost:8787
```

Optional environment variables: `GEOSNIPER_HOST` (default `127.0.0.1`),
`GEOSNIPER_PORT` (default `8787`).

## How it works

`server.py` is a small standard-library HTTP server that:

1. serves the web frontend (`web/`), and
2. proxies/aggregates NZP&M's public ArcGIS REST services (so there are no CORS
   or API-key hassles), and
3. does the point-in-polygon cross-referencing for snipe mode in pure Python.

The frontend is vanilla JavaScript + [Leaflet](https://leafletjs.com/) (loaded
from a CDN). Place search uses OpenStreetMap Nominatim, restricted to NZ.

### Data sources (all public NZP&M)

| Data | Service |
|------|---------|
| Active permits, applications, contest rounds, reserved areas | `Public/Permits_Minerals_Layers` |
| Historic mineral drill holes & exploration reports | `Public/GeodataCatalogue_Layers` |

Base URL: `https://gis.nzpam.govt.nz/server/rest/services/Public`

## Disclaimer

Data © New Zealand Petroleum & Minerals / MBIE and the respective report authors.
GeoSniper is an independent tool and is not affiliated with or endorsed by NZP&M
or MBIE. Historic "results" fields are as-recorded and are often incomplete or
marked *unknown* — always read the source report. Mineral permitting in NZ is
governed by the Crown Minerals Act; some land is excluded or subject to
iwi/Treaty and conservation constraints not fully reflected here.
