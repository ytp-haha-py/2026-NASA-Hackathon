import { useCallback, useEffect, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type {
  FeatureOut,
  NumericTarget,
  ReferenceOut,
  SearchResponse,
  SearchResultOut,
} from "../api/client";
import { useI18n } from "../i18n/I18nProvider";
import { PlanetMap, type PlanetBody } from "./PlanetMap";
import { RankingOverlay } from "./RankingOverlay";

type Basemap = "map" | "terrain" | "satellite";
type BodyView = "Earth" | PlanetBody;

const BODY_ORDER: BodyView[] = ["Earth", "Moon", "Mars"];

const BASEMAP_ORDER: Basemap[] = ["map", "terrain", "satellite"];

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
  /** Moon and Mars reference sites, shown on their own basemaps. */
  references?: ReferenceOut[];
  selectedReferenceId?: string | null;
};

const BASEMAPS: Record<Basemap, { url: string; maxZoom: number; attribution: string }> = {
  map: {
    url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  },
  // OpenTopoMap was dropped: it serves a "generic referrer" notice tile instead of terrain to
  // localhost and other generic referrers at zoom >= ~10 (#55). Esri tiles need no key.
  terrain: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
    maxZoom: 18,
    attribution:
      'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, USGS, NGA, EPA, USDA, NPS, and the GIS user community',
  },
  satellite: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    maxZoom: 18,
    attribution:
      'Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics, and the GIS user community',
  },
};

/** Room for the floating ranking panel on the left, so fitted pins are not hidden under it. */
const OVERLAY_PADDING: L.PointExpression = [340, 24];

/** A selected 1,980 m window fills a useful part of the map at this zoom without over-zooming. */
const SELECTED_MAX_ZOOM = 13;

function windowBounds(results: SearchResultOut[]): L.LatLngBounds {
  const bounds = L.latLngBounds([]);
  for (const result of results) {
    bounds.extend([result.bbox.south, result.bbox.west]);
    bounds.extend([result.bbox.north, result.bbox.east]);
  }
  return bounds;
}

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
  references = [],
  selectedReferenceId = null,
}: MapPanelProps) {
  const { t, tp, fmt, label } = useI18n();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const tilesRef = useRef<L.TileLayer | null>(null);
  const onSelectRef = useRef(onSelect);
  const [basemap, setBasemap] = useState<Basemap>("map");
  const [bodyView, setBodyView] = useState<BodyView>("Earth");
  const onEarth = bodyView === "Earth";
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

  const fitPadding = useCallback((): Pick<L.FitBoundsOptions, "paddingTopLeft" | "paddingBottomRight"> => {
    const wide = (containerRef.current?.clientWidth ?? 0) > 720;
    return { paddingTopLeft: wide ? OVERLAY_PADDING : [24, 24], paddingBottomRight: [24, 24] };
  }, []);

  const showAll = useCallback(() => {
    const map = mapRef.current;
    if (!map || results.length === 0) {
      return;
    }
    map.fitBounds(windowBounds(results), { ...fitPadding(), maxZoom: 9 });
  }, [results, fitPadding]);

  // Fit the view when the result set changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) {
      return;
    }
    if (results.length === 0) {
      map.setView([20, 0], 2);
      return;
    }
    showAll();
    // Only a new result set re-fits; selection is handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [results]);

  // Zoom to a window when the user selects it (results never auto-select, so the overview stays).
  useEffect(() => {
    const map = mapRef.current;
    const selected = results.find((result) => result.location_id === selectedId);
    if (!map || !selected) {
      return;
    }
    map.flyToBounds(windowBounds([selected]), {
      ...fitPadding(),
      maxZoom: SELECTED_MAX_ZOOM,
      duration: 0.6,
    });
    // Re-run on selection only; a new result set clears the selection in App.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

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
          fillOpacity: selected ? 0.08 : 0.06,
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

        {!onEarth && (
          <PlanetMap
            key={bodyView}
            body={bodyView}
            references={references}
            selectedReferenceId={selectedReferenceId}
            regionLabel={t("map.planetRegionLabel", { body: label.body(bodyView) })}
          />
        )}

        <div className="map-toggle map-body-toggle" role="group" aria-label={t("map.bodyLabel")}>
          {BODY_ORDER.map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={bodyView === value}
              className={bodyView === value ? "is-active" : ""}
              onClick={() => setBodyView(value)}
            >
              {label.body(value)}
            </button>
          ))}
        </div>

        <div
          className="map-toggle"
          role="group"
          aria-label={t("map.basemapLabel")}
          hidden={!onEarth}
        >
          {BASEMAP_ORDER.map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={basemap === value}
              className={basemap === value ? "is-active" : ""}
              onClick={() => setBasemap(value)}
            >
              {value === "map"
                ? t("map.basemapMap")
                : value === "terrain"
                  ? t("map.basemapTerrain")
                  : t("map.basemapSatellite")}
            </button>
          ))}
        </div>

        {onEarth && results.length > 0 && (
          <button type="button" className="map-show-all" onClick={showAll}>
            {t("map.showAll")}
          </button>
        )}

        {onEarth && response && (
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
