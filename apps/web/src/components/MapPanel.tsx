import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { FeatureOut, NumericTarget, SearchResponse, SearchResultOut } from "../api/client";
import { useI18n } from "../i18n/I18nProvider";
import { RankingOverlay } from "./RankingOverlay";

type Basemap = "map" | "terrain";

type MapPanelProps = {
  results: SearchResultOut[];
  selectedId: string | null;
  onSelect: (locationId: string) => void;
  /** When provided, the ranking window floats over the top-left corner of the map. */
  response?: SearchResponse;
  features?: FeatureOut[];
  target?: NumericTarget;
  /** Show the "nothing ranked yet" panel before the first search. */
  showIdle?: boolean;
};

const BASEMAPS: Record<Basemap, { url: string; maxZoom: number; attribution: string }> = {
  map: {
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  },
  terrain: {
    url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
    maxZoom: 17,
    attribution:
      'Map data &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, SRTM | Map style &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA)',
  },
};

/** Room for the floating ranking panel on the left, so fitted pins are not hidden under it. */
const OVERLAY_PADDING: L.PointExpression = [340, 24];

const COLORS = {
  earth: "#1d4ed8",
  selected: "#b93d0a",
};

function rankIcon(rank: number, selected: boolean): L.DivIcon {
  const size = selected ? 30 : 24;
  const pin = document.createElement("span");
  pin.className = `rank-pin${selected ? " is-selected" : ""}`;
  const text = document.createElement("span");
  text.textContent = String(rank);
  pin.append(text);
  return L.divIcon({
    className: "rank-pin-icon",
    html: pin,
    iconSize: [size, size],
    iconAnchor: [size / 2, size + 2],
    tooltipAnchor: [0, -size],
    popupAnchor: [0, -size - 4],
  });
}

function coordinateText(lat: number, lon: number): string {
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(lat).toFixed(2)}°${ns}  ${Math.abs(lon).toFixed(2)}°${ew}`;
}

export function MapPanel({
  results,
  selectedId,
  onSelect,
  response,
  features = [],
  target = {},
  showIdle = false,
}: MapPanelProps) {
  const { t, tp, fmt } = useI18n();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const tilesRef = useRef<L.TileLayer | null>(null);
  const onSelectRef = useRef(onSelect);
  const [basemap, setBasemap] = useState<Basemap>("map");
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
      zoomControl: false,
    });
    L.control.zoom({ position: "bottomright" }).addTo(map);
    L.control.scale({ position: "bottomleft", imperial: false }).addTo(map);
    mapRef.current = map;

    const layer = L.layerGroup().addTo(map);
    layerRef.current = layer;

    const resizeTimer = window.setTimeout(() => map.invalidateSize(), 0);

    return () => {
      window.clearTimeout(resizeTimer);
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
      tilesRef.current = null;
    };
  }, []);

  // Swap tile layers when the basemap toggle changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) {
      return;
    }
    tilesRef.current?.remove();
    const spec = BASEMAPS[basemap];
    const tiles = L.tileLayer(spec.url, { maxZoom: spec.maxZoom, attribution: spec.attribution });
    tiles.on("tileerror", () => setBasemapUnavailable(true));
    tiles.on("tileload", () => setBasemapUnavailable(false));
    tiles.addTo(map);
    tiles.bringToBack();
    tilesRef.current = tiles;
  }, [basemap]);

  // Fit the view only when the result set changes, not on every selection.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) {
      return;
    }
    if (results.length === 0) {
      map.setView([20, 0], 2);
      return;
    }
    const bounds = L.latLngBounds([]);
    for (const result of results) {
      bounds.extend([result.bbox.south, result.bbox.west]);
      bounds.extend([result.bbox.north, result.bbox.east]);
    }
    const wide = (containerRef.current?.clientWidth ?? 0) > 720;
    map.fitBounds(bounds, {
      paddingTopLeft: wide ? OVERLAY_PADDING : [24, 24],
      paddingBottomRight: [24, 24],
      maxZoom: 9,
    });
  }, [results]);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) {
      return;
    }

    layer.clearLayers();

    for (const result of results) {
      const selected = result.location_id === selectedId;
      const color = selected ? COLORS.selected : COLORS.earth;
      const rectangle = L.rectangle(
        [
          [result.bbox.south, result.bbox.west],
          [result.bbox.north, result.bbox.east],
        ],
        {
          color,
          weight: selected ? 2.5 : 1,
          fillColor: color,
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

      const tooltip = document.createElement("span");
      tooltip.textContent = t("map.tooltip", {
        rank: result.rank,
        name: result.display_name,
        distance: fmt.distance(result.distance),
      });
      marker.bindTooltip(tooltip, { direction: "top", offset: [0, -5] });
      marker.on("click", () => onSelectRef.current(result.location_id));
      marker.addTo(layer);

      if (selected) {
        const popup = document.createElement("div");
        popup.className = "map-popup";
        const head = document.createElement("div");
        head.className = "map-popup-head";
        const name = document.createElement("strong");
        name.textContent = result.display_name;
        const distance = document.createElement("span");
        distance.className = "mono";
        distance.textContent = `d ${fmt.distance(result.distance)}`;
        head.append(name, distance);
        const coords = document.createElement("div");
        coords.className = "mono map-popup-coords";
        coords.textContent = coordinateText(result.lat, result.lon);
        popup.append(head, coords);
        marker
          .bindPopup(popup, { closeButton: false, autoPan: false, className: "pae-popup" })
          .openPopup();
      }
    }
  }, [results, selectedId, t, fmt]);

  return (
    <section className="map-panel" aria-labelledby="map-heading">
      <h2 id="map-heading" className="sr-only">
        {t("map.heading")}
      </h2>

      <div className="map-stage">
        <div
          ref={containerRef}
          className="map-canvas"
          data-testid="leaflet-map"
          role="region"
          aria-label={t("map.regionLabel")}
        />

        <div className="map-toggle" role="group" aria-label={t("map.basemapLabel")}>
          {(["map", "terrain"] as const).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={basemap === value}
              className={basemap === value ? "is-active" : ""}
              onClick={() => setBasemap(value)}
            >
              {value === "map" ? t("map.basemapMap") : t("map.basemapTerrain")}
            </button>
          ))}
        </div>

        {response && (
          <RankingOverlay
            response={response}
            features={features}
            target={target}
            selectedId={selectedId}
            onSelect={onSelect}
          />
        )}
        {!response && showIdle && (
          <section className="ranking-overlay is-idle" aria-label={t("overlay.regionLabel")}>
            <p className="ranking-overlay-empty">{t("overlay.idle")}</p>
          </section>
        )}
      </div>

      {basemapUnavailable && (
        <p className="map-status" role="status">
          {t("map.basemapUnavailable")}
        </p>
      )}

      <p className="map-note">
        {results.length === 0 ? t("map.noWindows") : tp("map.plotted", results.length)}{" "}
        <span className="map-attribution-note">{t("map.attribution")}</span>
      </p>
    </section>
  );
}
