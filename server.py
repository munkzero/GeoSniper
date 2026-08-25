#!/usr/bin/env python3
"""GeoSniper backend.

A zero-dependency (Python standard library only) HTTP server that:
  * serves the GeoSniper web frontend, and
  * proxies / aggregates live data from New Zealand Petroleum & Minerals
    (NZP&M) public ArcGIS REST services, and
  * computes "sniping" opportunities: historic gold/mineral ground that is
    NOT currently covered by an active permit, plus active permits that are
    about to expire (land that is about to re-open).

Data source: https://www.nzpam.govt.nz  (public ArcGIS services)

Run:
    python3 server.py            # then open http://localhost:8787
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = os.environ.get("GEOSNIPER_HOST", "127.0.0.1")
PORT = int(os.environ.get("GEOSNIPER_PORT", "8787"))
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

# --- NZP&M public ArcGIS REST endpoints ------------------------------------
NZPAM_BASE = "https://gis.nzpam.govt.nz/server/rest/services/Public"
PERMITS_FS = NZPAM_BASE + "/Permits_Minerals_Layers/FeatureServer"
GEODATA_FS = NZPAM_BASE + "/GeodataCatalogue_Layers/FeatureServer"

# Layer indexes within the permits feature server.
L_APPLICATIONS = 0   # Minerals Permit Applications (pending)
L_ACTIVE = 1         # Mineral Active Permits (granted / live)
L_OPEN_CONTEST = 4   # Open Contest Round (blocks NZP&M is offering now)
L_RESERVED = 6       # Reserved Area

# Layer indexes within the geodata catalogue feature server.
L_DRILLHOLES = 2     # Mineral Drill holes (historic exploration, points)
L_REPORTS = 3        # Mineral Reports (historic exploration, polygons)

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Small in-memory cache so repeated map pans don't hammer NZP&M.
_CACHE: dict[str, tuple[float, object]] = {}
_CACHE_TTL = 120  # seconds


def _cache_get(key: str):
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]
    return None


def _cache_put(key: str, value) -> None:
    _CACHE[key] = (time.time(), value)


def arcgis_query(feature_server: str, layer: int, params: dict) -> dict:
    """Run an ArcGIS REST /query and return the parsed JSON."""
    q = {
        "f": "json",
        "outSR": "4326",
        "returnGeometry": "true",
        "where": "1=1",
        "outFields": "*",
    }
    q.update(params)
    url = f"{feature_server}/{layer}/query?" + urllib.parse.urlencode(q)
    cache_key = url
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    _cache_put(cache_key, data)
    return data


def envelope(bbox: str | None) -> dict:
    """Turn a 'minLon,minLat,maxLon,maxLat' string into ArcGIS geom params."""
    if not bbox:
        return {}
    try:
        min_lon, min_lat, max_lon, max_lat = (float(x) for x in bbox.split(","))
    except (ValueError, TypeError):
        return {}
    geom = {"xmin": min_lon, "ymin": min_lat, "xmax": max_lon, "ymax": max_lat}
    return {
        "geometry": json.dumps(geom),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
    }


def ms_to_iso(ms) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
    except (ValueError, OSError, TypeError):
        return None


def esri_polygon_to_geojson(geom: dict) -> dict | None:
    rings = geom.get("rings") if geom else None
    if not rings:
        return None
    return {"type": "Polygon" if len(rings) == 1 else "MultiPolygon",
            "coordinates": rings if len(rings) == 1 else [[r] for r in rings]}


def like(value: str) -> str:
    """Escape a value for use inside an ArcGIS LIKE clause."""
    return value.replace("'", "''")


# --- opportunity scoring ----------------------------------------------------

def point_in_ring(lon: float, lat: float, ring: list) -> bool:
    """Ray-casting point-in-polygon for a single ring of [lon,lat] pairs."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (
            lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi
        ):
            inside = not inside
        j = i
    return inside


def point_in_permits(lon: float, lat: float, permits: list) -> dict | None:
    """Return the first active permit polygon that contains the point."""
    for p in permits:
        for ring in p["rings"]:
            if point_in_ring(lon, lat, ring):
                return p
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "GeoSniper/1.0"

    def log_message(self, fmt, *args):  # quieter logging
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers -----------------------------------------------------------
    def _send_json(self, obj, status: int = 200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: str):
        if not os.path.isfile(path):
            self.send_error(404, "Not found")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".ico": "image/x-icon",
        }.get(os.path.splitext(path)[1], "application/octet-stream")
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routing -----------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        route = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        try:
            if route == "/" or route == "/index.html":
                return self._send_file(os.path.join(WEB_DIR, "index.html"))
            if route.startswith("/web/"):
                rel = route[len("/web/"):]
                return self._send_file(os.path.join(WEB_DIR, rel))
            if route in ("/app.js", "/style.css"):
                return self._send_file(os.path.join(WEB_DIR, route.lstrip("/")))

            if route == "/api/permits":
                return self._api_permits(qs)
            if route == "/api/applications":
                return self._api_applications(qs)
            if route == "/api/opportunities-blocks":
                return self._api_open_blocks(qs)
            if route == "/api/drillholes":
                return self._api_drillholes(qs)
            if route == "/api/reports":
                return self._api_reports(qs)
            if route == "/api/snipe":
                return self._api_snipe(qs)
            if route == "/api/geocode":
                return self._api_geocode(qs)

            self.send_error(404, "Not found")
        except urllib.error.URLError as exc:
            self._send_json({"error": f"upstream NZP&M request failed: {exc}"}, 502)
        except Exception as exc:  # noqa: BLE001 - surface any error to the client
            self._send_json({"error": str(exc)}, 500)

    # -- API implementations ----------------------------------------------
    def _mineral_where(self, qs, field="Minerals"):
        clauses = []
        mineral = (qs.get("mineral", [""])[0]).strip()
        if mineral and mineral.lower() != "all":
            clauses.append(f"{field} LIKE '%{like(mineral)}%'")
        ptype = (qs.get("permit_type", [""])[0]).strip()
        # Map a friendly category onto the Permit_Type_Description text.
        type_map = {
            "mining": "%Mining%",
            "exploration": "%Exploration%",
            "prospecting": "%Prospecting%",
            "fossicking": "%Fossicking%",
        }
        pat = type_map.get(ptype.lower())
        if pat:
            clauses.append(f"Permit_Type_Description LIKE '{pat}'")
        return " AND ".join(clauses) if clauses else "1=1"

    def _api_permits(self, qs):
        bbox = qs.get("bbox", [None])[0]
        params = {
            "where": self._mineral_where(qs),
            "outFields": ("Permit_Number,Minerals,Commodity,Permit_Status,"
                          "Permit_Type_Description,Operator,Owners,"
                          "Permit_Location,Permit_Area,Permit_Expiry_Date,"
                          "Permit_Grant_Date"),
        }
        params.update(envelope(bbox))
        data = arcgis_query(PERMITS_FS, L_ACTIVE, params)
        features = []
        now = datetime.now(tz=timezone.utc)
        for f in data.get("features", []):
            a = f["attributes"]
            geom = esri_polygon_to_geojson(f.get("geometry"))
            if not geom:
                continue
            expiry = ms_to_iso(a.get("Permit_Expiry_Date"))
            days = None
            if a.get("Permit_Expiry_Date"):
                days = int((a["Permit_Expiry_Date"] / 1000 -
                            now.timestamp()) / 86400)
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "permit": a.get("Permit_Number"),
                    "mineral": a.get("Minerals"),
                    "type": a.get("Permit_Type_Description"),
                    "operator": a.get("Operator") or a.get("Owners"),
                    "location": a.get("Permit_Location"),
                    "area_km2": a.get("Permit_Area"),
                    "expiry": expiry,
                    "days_to_expiry": days,
                    "status": a.get("Permit_Status"),
                },
            })
        self._send_json({"type": "FeatureCollection", "features": features})

    def _api_applications(self, qs):
        bbox = qs.get("bbox", [None])[0]
        params = {
            "where": self._mineral_where(qs),
            "outFields": ("Permit_Number,Minerals,Permit_Type_Description,"
                          "Operator,Owners,Permit_Location"),
        }
        params.update(envelope(bbox))
        data = arcgis_query(PERMITS_FS, L_APPLICATIONS, params)
        features = []
        for f in data.get("features", []):
            a = f["attributes"]
            geom = esri_polygon_to_geojson(f.get("geometry"))
            if not geom:
                continue
            features.append({
                "type": "Feature", "geometry": geom,
                "properties": {
                    "permit": a.get("Permit_Number"),
                    "mineral": a.get("Minerals"),
                    "type": a.get("Permit_Type_Description"),
                    "operator": a.get("Operator") or a.get("Owners"),
                    "location": a.get("Permit_Location"),
                },
            })
        self._send_json({"type": "FeatureCollection", "features": features})

    def _api_open_blocks(self, qs):
        """Open contest rounds + reserved areas = ground NZP&M is offering."""
        out = []
        for layer, kind in ((L_OPEN_CONTEST, "open_contest"),
                            (L_RESERVED, "reserved")):
            data = arcgis_query(PERMITS_FS, layer, {"outFields": "*"})
            for f in data.get("features", []):
                geom = esri_polygon_to_geojson(f.get("geometry"))
                if not geom:
                    continue
                a = f["attributes"]
                out.append({
                    "type": "Feature", "geometry": geom,
                    "properties": {"kind": kind, "detail": a},
                })
        self._send_json({"type": "FeatureCollection", "features": out})

    def _api_drillholes(self, qs):
        bbox = qs.get("bbox", [None])[0]
        commodity = (qs.get("commodity", ["Gold"])[0]).strip()
        clauses = []
        if commodity and commodity.lower() != "all":
            c = like(commodity)
            clauses.append(
                f"(Prospect_Field LIKE '%{c}%' OR Purpose LIKE '%{c}%')")
        where = " AND ".join(clauses) if clauses else "1=1"
        params = {
            "where": where,
            "outFields": ("Title,Prospect_Field,Purpose,Result_Public,"
                          "Total_Depth_Public,Permit,Operator,End_Date,"
                          "Latitude,Longitude"),
            "resultRecordCount": qs.get("limit", ["2000"])[0],
        }
        params.update(envelope(bbox))
        data = arcgis_query(GEODATA_FS, L_DRILLHOLES, params)
        features = []
        for f in data.get("features", []):
            a = f["attributes"]
            g = f.get("geometry") or {}
            lon = g.get("x") or a.get("Longitude")
            lat = g.get("y") or a.get("Latitude")
            if lon is None or lat is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "title": a.get("Title"),
                    "field": a.get("Prospect_Field"),
                    "purpose": a.get("Purpose"),
                    "result": a.get("Result_Public"),
                    "depth_m": a.get("Total_Depth_Public"),
                    "permit": a.get("Permit"),
                    "operator": a.get("Operator"),
                    "end_date": ms_to_iso(a.get("End_Date")),
                },
            })
        self._send_json({"type": "FeatureCollection", "features": features})

    def _api_reports(self, qs):
        bbox = qs.get("bbox", [None])[0]
        commodity = (qs.get("commodity", ["Gold"])[0]).strip()
        where = "1=1"
        if commodity and commodity.lower() != "all":
            where = f"Commodity LIKE '%{like(commodity)}%'"
        params = {
            "where": where,
            "outFields": ("Report_ID,Title,Author,Summary,Commodity,Operator,"
                          "Permit,Region,Start_Date,End_Date,Open_File"),
            "resultRecordCount": qs.get("limit", ["1000"])[0],
        }
        params.update(envelope(bbox))
        data = arcgis_query(GEODATA_FS, L_REPORTS, params)
        features = []
        for f in data.get("features", []):
            a = f["attributes"]
            geom = esri_polygon_to_geojson(f.get("geometry"))
            if not geom:
                continue
            features.append({
                "type": "Feature", "geometry": geom,
                "properties": {
                    "report_id": a.get("Report_ID"),
                    "title": a.get("Title"),
                    "author": a.get("Author"),
                    "summary": a.get("Summary"),
                    "commodity": a.get("Commodity"),
                    "operator": a.get("Operator"),
                    "permit": a.get("Permit"),
                    "region": a.get("Region"),
                    "start_date": ms_to_iso(a.get("Start_Date")),
                    "end_date": ms_to_iso(a.get("End_Date")),
                    "open_file": a.get("Open_File"),
                },
            })
        self._send_json({"type": "FeatureCollection", "features": features})

    def _api_geocode(self, qs):
        """Place search restricted to New Zealand (OpenStreetMap Nominatim)."""
        query = (qs.get("q", [""])[0]).strip()
        if not query:
            return self._send_json({"results": []})
        params = {
            "q": query,
            "format": "jsonv2",
            "countrycodes": "nz",
            "limit": "6",
            "addressdetails": "1",
        }
        url = "https://nominatim.openstreetmap.org/search?" + \
            urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={
            "User-Agent": "GeoSniper/1.0 (NZ mineral permit finder)"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
        results = []
        for r in raw:
            bb = r.get("boundingbox")  # [minLat, maxLat, minLon, maxLon]
            bbox = None
            if bb and len(bb) == 4:
                bbox = [float(bb[2]), float(bb[0]),
                        float(bb[3]), float(bb[1])]
            results.append({
                "name": r.get("display_name"),
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "bbox": bbox,
                "type": r.get("type"),
            })
        self._send_json({"results": results})

    def _api_snipe(self, qs):
        """The core feature.

        Within the requested bbox, cross-reference historic mineral drill
        holes against currently-active permits and classify each drill hole:

          * OPEN     - historic drilling on ground with NO active permit.
          * EXPIRING - covered by an active permit expiring within N days;
                       the ground is about to re-open.
          * COVERED  - covered by an active permit with plenty of time left.
        """
        bbox = qs.get("bbox", [None])[0]
        if not bbox:
            return self._send_json(
                {"error": "bbox required for /api/snipe"}, 400)
        commodity = (qs.get("commodity", ["Gold"])[0]).strip()
        try:
            expiring_days = int(qs.get("expiring_days", ["365"])[0])
        except ValueError:
            expiring_days = 365

        # 1) active permits in view (any mineral - a permit blocks the ground
        #    regardless of what it targets).
        perm_params = {"outFields": ("Permit_Number,Minerals,"
                                     "Permit_Type_Description,Operator,"
                                     "Permit_Expiry_Date")}
        perm_params.update(envelope(bbox))
        perm_data = arcgis_query(PERMITS_FS, L_ACTIVE, perm_params)
        now = datetime.now(tz=timezone.utc).timestamp()
        permits = []
        for f in perm_data.get("features", []):
            rings = (f.get("geometry") or {}).get("rings")
            if not rings:
                continue
            a = f["attributes"]
            exp = a.get("Permit_Expiry_Date")
            permits.append({
                "rings": rings,
                "permit": a.get("Permit_Number"),
                "mineral": a.get("Minerals"),
                "type": a.get("Permit_Type_Description"),
                "operator": a.get("Operator"),
                "expiry_ms": exp,
                "expiry": ms_to_iso(exp),
                "days_to_expiry": (int((exp / 1000 - now) / 86400)
                                   if exp else None),
            })

        # 2) historic drill holes in view.
        clauses = []
        if commodity and commodity.lower() != "all":
            c = like(commodity)
            clauses.append(
                f"(Prospect_Field LIKE '%{c}%' OR Purpose LIKE '%{c}%')")
        dh_params = {
            "where": " AND ".join(clauses) if clauses else "1=1",
            "outFields": ("Title,Prospect_Field,Result_Public,"
                          "Total_Depth_Public,Permit,Operator,End_Date"),
            "resultRecordCount": qs.get("limit", ["3000"])[0],
        }
        dh_params.update(envelope(bbox))
        dh_data = arcgis_query(GEODATA_FS, L_DRILLHOLES, dh_params)

        features = []
        counts = {"open": 0, "expiring": 0, "covered": 0}
        for f in dh_data.get("features", []):
            g = f.get("geometry") or {}
            lon, lat = g.get("x"), g.get("y")
            if lon is None or lat is None:
                continue
            a = f["attributes"]
            hit = point_in_permits(lon, lat, permits)
            if hit is None:
                status = "open"
                cover = None
            elif (hit["days_to_expiry"] is not None and
                  0 <= hit["days_to_expiry"] <= expiring_days):
                status = "expiring"
                cover = hit
            else:
                status = "covered"
                cover = hit
            counts[status] += 1
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "status": status,
                    "title": a.get("Title"),
                    "field": a.get("Prospect_Field"),
                    "result": a.get("Result_Public"),
                    "depth_m": a.get("Total_Depth_Public"),
                    "hist_permit": a.get("Permit"),
                    "hist_operator": a.get("Operator"),
                    "end_date": ms_to_iso(a.get("End_Date")),
                    "covering_permit": cover["permit"] if cover else None,
                    "covering_expiry": cover["expiry"] if cover else None,
                    "covering_days_left": (cover["days_to_expiry"]
                                           if cover else None),
                    "covering_operator": cover["operator"] if cover else None,
                },
            })

        self._send_json({
            "type": "FeatureCollection",
            "features": features,
            "summary": counts,
            "active_permits_in_view": len(permits),
            "expiring_days": expiring_days,
            "commodity": commodity,
        })


def main():
    if not os.path.isdir(WEB_DIR):
        sys.exit(f"web directory not found: {WEB_DIR}")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"GeoSniper running -> http://{HOST}:{PORT}")
    print("Data: New Zealand Petroleum & Minerals (public ArcGIS services)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        httpd.shutdown()


if __name__ == "__main__":
    main()
