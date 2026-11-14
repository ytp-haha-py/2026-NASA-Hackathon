import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { SearchResultOut } from "../api/client";
import { formatDistance } from "../lib/format";

type MapPanelProps = {
  results: SearchResultOut[];
  selectedId: string | null;
  onSelect: (locationId: string) => void;
};

export function MapPanel({ results, selectedId, onSelect }: MapPanelProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const onSelectRef = useRef(onSelect);
  const [basemapUnavailable, setBasemapUnavailable] = useState(
    () => typeof navigator !== "undefined" && navigator.onLine === false,
  );

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }

    const map = L.map(container, {
      center: [20, 0],
      zoom: 2,
      minZoom: 2,
      worldCopyJump: true,
    });
    mapRef.current = map;

    const tiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    });
    tiles.on("tileerror", () => setBasemapUnavailable(true));
    tiles.on("tileload", () => setBasemapUnavailable(false));
    tiles.addTo(map);

    const layer = L.layerGroup().addTo(map);
    layerRef.current = layer;

    const resizeTimer = window.setTimeout(() => map.invalidateSize(), 0);

    return () => {
      window.clearTimeout(resizeTimer);
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) {
      return;
    }

    layer.clearLayers();

    if (results.length === 0) {
      map.setView([20, 0], 2);
      return;
    }

    const bounds = L.latLngBounds([]);

    for (const result of results) {
      const selected = result.location_id === selectedId;
      const rectangle = L.rectangle(
        [
          [result.bbox.south, result.bbox.west],
          [result.bbox.north, result.bbox.east],
        ],
        {
          color: selected ? "#a33a12" : "#285c4d",
          weight: selected ? 3 : 1.5,
          fillColor: selected ? "#d97736" : "#3d7a65",
          fillOpacity: selected ? 0.24 : 0.1,
        },
      );
      rectangle.on("click", () => onSelectRef.current(result.location_id));
      rectangle.addTo(layer);

      const marker = L.circleMarker([result.lat, result.lon], {
        radius: selected ? 9 : 6,
        color: selected ? "#74250b" : "#173d33",
        weight: 2,
        fillColor: selected ? "#e28a4a" : "#4f9078",
        fillOpacity: 0.95,
      });

      const label = document.createElement("span");
      label.textContent = `#${result.rank} ${result.display_name} · distance ${formatDistance(result.distance)}`;
      marker.bindTooltip(label, { direction: "top", offset: [0, -5] });
      marker.on("click", () => onSelectRef.current(result.location_id));
      marker.addTo(layer);

      bounds.extend([result.bbox.south, result.bbox.west]);
      bounds.extend([result.bbox.north, result.bbox.east]);
    }

    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 9 });
  }, [results, selectedId]);

  return (
    <section className="map-panel" aria-labelledby="map-heading">
      <div className="section-heading compact">
        <p className="eyebrow">Spatial view</p>
        <h2 id="map-heading">Indexed result windows</h2>
        <p className="muted">
          Only returned results are plotted. Pins and rectangles are not an exhaustive
          index, and the basemap is for orientation only — not scientific evidence.
        </p>
      </div>

      <div
        ref={containerRef}
        className="map-canvas"
        data-testid="leaflet-map"
        role="region"
        aria-label="Map of returned indexed Earth candidate windows"
      />

      {basemapUnavailable && (
        <p className="map-status" role="status">
          Basemap tiles are unavailable (offline or blocked). The ranked list and record
          details remain fully usable without the basemap.
        </p>
      )}

      <p className="map-note">
        {results.length === 0
          ? "No result windows to plot yet."
          : `${results.length} returned window${results.length === 1 ? "" : "s"} plotted. Click a pin or window rectangle to select it. Zoom in to see the 1,980 m window rectangles at this scale.`}
      </p>
      <p className="map-attribution-note">
        Basemap © OpenStreetMap contributors, available under the Open Database License.
      </p>
    </section>
  );
}
