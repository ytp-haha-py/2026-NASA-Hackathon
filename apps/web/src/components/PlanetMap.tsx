import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { ReferenceOut } from "../api/client";

export type PlanetBody = "Moon" | "Mars";

type PlanetMapProps = {
  body: PlanetBody;
  references: ReferenceOut[];
  selectedReferenceId: string | null;
  regionLabel: string;
};

/**
 * NASA Trek global mosaics, served as equirectangular (EPSG:4326) WMTS tiles. The polar lunar
 * sites sit at 85–90°S, which Web Mercator cannot show at all, so these maps use the
 * geographic CRS instead of Leaflet's default.
 */
const BODY_TILES: Record<PlanetBody, { url: string; maxNativeZoom: number; attribution: string }> = {
  Moon: {
    url: "https://trek.nasa.gov/tiles/Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02/1.0.0/default/default028mm/{z}/{y}/{x}.jpg",
    maxNativeZoom: 8,
    attribution:
      'LRO WAC mosaic: NASA/GSFC/Arizona State University, via <a href="https://trek.nasa.gov/moon/">NASA Moon Trek</a>',
  },
  Mars: {
    url: "https://trek.nasa.gov/tiles/Mars/EQ/Mars_Viking_MDIM21_ClrMosaic_global_232m/1.0.0/default/default028mm/{z}/{y}/{x}.jpg",
    maxNativeZoom: 7,
    attribution:
      'Viking MDIM 2.1 mosaic: NASA/JPL/USGS, via <a href="https://trek.nasa.gov/mars/">NASA Mars Trek</a>',
  },
};

const WORLD: L.LatLngBoundsExpression = [
  [-90, -180],
  [90, 180],
];

const PIN_COLOR = "#b93d0a";

function coordinateText(lat: number, lon: number): string {
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(2)}°${ns}  ${Math.abs(lon).toFixed(2)}°${ew}`;
}

/** Reference sites of one body on that body's own basemap. Mount with `key={body}`. */
export function PlanetMap({ body, references, selectedReferenceId, regionLabel }: PlanetMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const spec = BODY_TILES[body];
    const map = L.map(container, {
      crs: L.CRS.EPSG4326,
      center: [0, 0],
      zoom: 1,
      minZoom: 1,
      maxZoom: spec.maxNativeZoom + 2,
      maxBounds: WORLD,
      maxBoundsViscosity: 1,
      zoomControl: false,
    });
    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.tileLayer(spec.url, {
      maxNativeZoom: spec.maxNativeZoom,
      maxZoom: spec.maxNativeZoom + 2,
      noWrap: true,
      bounds: WORLD,
      attribution: spec.attribution,
    }).addTo(map);
    mapRef.current = map;
    layerRef.current = L.layerGroup().addTo(map);
    const resizeTimer = window.setTimeout(() => map.invalidateSize(), 0);

    return () => {
      window.clearTimeout(resizeTimer);
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, [body]);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) {
      return;
    }
    layer.clearLayers();

    const sites = references.flatMap((reference) =>
      reference.body === body && typeof reference.lat === "number" && typeof reference.lon === "number"
        ? [{ ...reference, lat: reference.lat, lon: reference.lon }]
        : [],
    );
    if (sites.length === 0) {
      map.setView([0, 0], 1);
      return;
    }

    const bounds = L.latLngBounds([]);
    for (const site of sites) {
      const selected = site.reference_id === selectedReferenceId;
      const position: L.LatLngTuple = [site.lat, site.lon];
      bounds.extend(position);
      const marker = L.circleMarker(position, {
        radius: selected ? 9 : 6,
        color: "#ffffff",
        weight: 2,
        fillColor: PIN_COLOR,
        fillOpacity: selected ? 1 : 0.85,
      });
      const tooltip = document.createElement("span");
      tooltip.textContent = site.display_name;
      marker.bindTooltip(tooltip, { direction: "top", offset: [0, -6] });
      marker.addTo(layer);

      if (selected) {
        const popup = document.createElement("div");
        popup.className = "map-popup";
        const name = document.createElement("strong");
        name.textContent = site.display_name;
        const coords = document.createElement("div");
        coords.className = "mono map-popup-coords";
        coords.textContent = coordinateText(site.lat, site.lon);
        popup.append(name, coords);
        marker
          .bindPopup(popup, { closeButton: false, autoPan: false, className: "pae-popup" })
          .openPopup();
      }
    }

    const selectedSite = sites.find((site) => site.reference_id === selectedReferenceId);
    if (selectedSite) {
      map.setView([selectedSite.lat, selectedSite.lon], Math.min(5, BODY_TILES[body].maxNativeZoom));
    } else {
      map.fitBounds(bounds.pad(0.4), { maxZoom: 5 });
    }
  }, [body, references, selectedReferenceId]);

  return (
    <div
      ref={containerRef}
      className="map-canvas planet-canvas"
      data-testid={`planet-map-${body.toLowerCase()}`}
      role="region"
      aria-label={regionLabel}
    />
  );
}
