import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type {
  FeatureOut,
  NumericTarget,
  SearchResponse,
  SearchResultOut,
} from "../api/client";
import { useI18n } from "../i18n/I18nProvider";
import { RankingOverlay } from "./RankingOverlay";

type MapPanelProps = {
  results: SearchResultOut[];
  selectedId: string | null;
  onSelect: (locationId: string) => void;
  /** When provided, the ranking window floats over the top-left corner of the map. */
  response?: SearchResponse;
  features?: FeatureOut[];
  target?: NumericTarget;
};

function rankIcon(rank: number, selected: boolean): L.DivIcon {
  const size = selected ? 32 : 26;
  const pin = document.createElement("span");
  pin.className = `rank-pin${selected ? " is-selected" : ""}`;
  const text = document.createElement("span");
  text.textContent = String(rank);
  pin.append(text);
  return L.divIcon({
    className: "rank-pin-icon",
    html: pin,
    iconSize: [size, size],
    iconAnchor: [size / 2, size],
    tooltipAnchor: [0, -size],
  });
}

export function MapPanel({
  results,
  selectedId,
  onSelect,
  response,
  features = [],
  target = {},
}: MapPanelProps) {
  const { t, tp, fmt } = useI18n();
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
          color: selected ? "#b93d0a" : "#1d4ed8",
          weight: selected ? 2.5 : 1,
          fillColor: selected ? "#b93d0a" : "#1d4ed8",
          fillOpacity: selected ? 0.18 : 0.06,
        },
      );
      rectangle.on("click", () => onSelectRef.current(result.location_id));
      rectangle.addTo(layer);

      const marker = L.marker([result.lat, result.lon], {
        icon: rankIcon(result.rank, selected),
        zIndexOffset: selected ? 1000 : 0,
        keyboard: false,
      });

      const label = document.createElement("span");
      label.textContent = t("map.tooltip", {
        rank: result.rank,
        name: result.display_name,
        distance: fmt.distance(result.distance),
      });
      marker.bindTooltip(label, { direction: "top", offset: [0, -5] });
      marker.on("click", () => onSelectRef.current(result.location_id));
      marker.addTo(layer);

      bounds.extend([result.bbox.south, result.bbox.west]);
      bounds.extend([result.bbox.north, result.bbox.east]);
    }

    map.fitBounds(bounds, { padding: [30, 30], maxZoom: 9 });
  }, [results, selectedId, t, fmt]);

  return (
    <section className="map-panel" aria-labelledby="map-heading">
      <div className="section-heading compact">
        <h2 id="map-heading">{t("map.heading")}</h2>
      </div>

      <div className="map-stage">
        <div
          ref={containerRef}
          className="map-canvas"
          data-testid="leaflet-map"
          role="region"
          aria-label={t("map.regionLabel")}
        />
        {response && (
          <RankingOverlay
            response={response}
            features={features}
            target={target}
            selectedId={selectedId}
            onSelect={onSelect}
          />
        )}
      </div>

      {basemapUnavailable && (
        <p className="map-status" role="status">
          {t("map.basemapUnavailable")}
        </p>
      )}

      <p className="map-note">
        {results.length === 0 ? t("map.noWindows") : tp("map.plotted", results.length)}
      </p>
      <p className="map-attribution-note">{t("map.attribution")}</p>
    </section>
  );
}
