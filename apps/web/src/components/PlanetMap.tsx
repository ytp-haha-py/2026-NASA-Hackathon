import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { ReferenceOut } from "../api/client";

export type PlanetBody = "Moon" | "Mars";

type MapReference = ReferenceOut & { lat: number; lon: number };

export function hasMapCoordinates(reference: ReferenceOut): reference is MapReference {
  return Number.isFinite(reference.lat) && Number.isFinite(reference.lon);
}

type PlanetMapProps = {
  body: PlanetBody;
  references: MapReference[];
  selectedReferenceId: string | null;
  onSelectReference: (id: string) => void;
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

const WORLD: L.LatLngTuple[] = [
  [-90, -180],
  [90, 180],
];

const PIN_SIZE = 24;
const MAP_PADDING: L.PointExpression = [24, 24];

export function PlanetMap({
  body,
  references,
  selectedReferenceId,
  onSelectReference,
  regionLabel,
}: PlanetMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const pins = useRef(new Map<string, HTMLButtonElement>());

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const spec = BODY_TILES[body];
    // The closest lunar ROIs still overlap at the imagery's native zoom.
    const maxZoom = body === "Moon" ? 14 : spec.maxNativeZoom + 2;
    const map = L.map(container, {
      crs: L.CRS.EPSG4326,
      center: [0, 0],
      zoom: 1,
      minZoom: 1,
      maxZoom,
      maxBounds: L.latLngBounds(WORLD).pad(0.1),
      maxBoundsViscosity: 1,
      zoomControl: false,
    });
    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.tileLayer(spec.url, {
      maxNativeZoom: spec.maxNativeZoom,
      maxZoom,
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
    pins.current.clear();
    const sites = references.filter((reference) => reference.body === body);
    if (sites.length === 0) {
      map.setView([0, 0], 1);
      return;
    }

    const bounds = L.latLngBounds([]);
    for (const site of sites) {
      const position = L.latLng(site.lat, site.lon);
      bounds.extend(position);
      const pin = document.createElement("button");
      pin.type = "button";
      pin.className = "planet-reference-pin";
      pin.setAttribute("aria-label", site.display_name);
      L.DomEvent.disableClickPropagation(pin);
      pins.current.set(site.reference_id, pin);

      const marker = L.marker(position, {
        keyboard: false,
        icon: L.divIcon({
          className: "planet-reference-marker",
          html: pin,
          iconSize: [PIN_SIZE, PIN_SIZE],
          iconAnchor: [PIN_SIZE / 2, PIN_SIZE / 2],
        }),
      });
      const tooltip = document.createElement("span");
      tooltip.textContent = site.display_name;
      marker.bindTooltip(tooltip, { direction: "top", offset: [0, -10] });
      pin.addEventListener("click", (event) => {
        // A keyboard press already identifies one named button.
        if (event.detail > 0 && map.getZoom() < map.getMaxZoom()) {
          const point = map.project(position);
          const nearby = sites.filter((other) =>
            other !== site && point.distanceTo(map.project([other.lat, other.lon])) < PIN_SIZE,
          );
          if (nearby.length > 0) {
            const group = L.latLngBounds([position]);
            nearby.forEach((other) => group.extend([other.lat, other.lon]));
            marker.closeTooltip();
            map.fitBounds(group, { padding: MAP_PADDING, animate: false });
            return;
          }
        }
        onSelectReference(site.reference_id);
      });
      marker.addTo(layer);
    }
    map.fitBounds(bounds, { maxZoom: 5, padding: MAP_PADDING, animate: false });
  }, [body, references, onSelectReference]);

  useEffect(() => {
    for (const [id, pin] of pins.current) {
      pin.setAttribute("aria-pressed", String(id === selectedReferenceId));
    }
  }, [body, references, onSelectReference, selectedReferenceId]);

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
