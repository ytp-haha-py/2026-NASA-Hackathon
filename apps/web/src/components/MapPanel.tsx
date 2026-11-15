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
import { addEarthBasemap, type EarthBasemap } from "../lib/earthBasemap";
import { PlanetMap, type PlanetBody } from "./PlanetMap";
import { RankingOverlay } from "./RankingOverlay";

type BodyView = "Earth" | PlanetBody;

const BODY_ORDER: BodyView[] = ["Earth", "Moon", "Mars"];

const BASEMAP_ORDER: EarthBasemap[] = ["map", "terrain", "satellite"];

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
  /** Area to outline (drawn here or typed in the analysis form). */
  aoiBbox?: MapBbox | null;
  /** Enables the "Select area" tool on the Earth map. */
  onDrawBbox?: (bbox: MapBbox) => void;
};

export type MapBbox = { west: number; south: number; east: number; north: number };

const MIN_DRAW_PX = 6;

function wrapLongitude(lon: number): number {
  return ((((lon + 180) % 360) + 360) % 360) - 180;
}

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
  aoiBbox = null,
  onDrawBbox,
}: MapPanelProps) {
  const { locale, t, tp, fmt, label } = useI18n();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const onSelectRef = useRef(onSelect);
  const [basemap, setBasemap] = useState<EarthBasemap>("map");
  const [bodyView, setBodyView] = useState<BodyView>("Earth");
  const onEarth = bodyView === "Earth";
  const [drawing, setDrawing] = useState(false);
  const aoiLayerRef = useRef<L.Rectangle | null>(null);
  const onDrawRef = useRef(onDrawBbox);
  const [basemapUnavailable, setBasemapUnavailable] = useState(
    () => typeof navigator !== "undefined" && navigator.onLine === false,
  );

  useEffect(() => {
    onSelectRef.current = onSelect;
    onDrawRef.current = onDrawBbox;
  }, [onSelect, onDrawBbox]);

  // Outline the area chosen for analysis.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) {
      return;
    }
    aoiLayerRef.current?.remove();
    aoiLayerRef.current = null;
    if (!aoiBbox) {
      return;
    }
    aoiLayerRef.current = L.rectangle(
      [
        [aoiBbox.south, aoiBbox.west],
        [aoiBbox.north, aoiBbox.east],
      ],
      { color: COLORS.selected, weight: 2, dashArray: "6 4", fillOpacity: 0.1, interactive: false },
    ).addTo(map);
  }, [aoiBbox]);

  // Rubber-band selection. Pointer events so mouse, pen and touch all work.
  useEffect(() => {
    const map = mapRef.current;
    const container = containerRef.current;
    if (!drawing || !onEarth || !map || !container) {
      return;
    }
    let start: L.LatLng | null = null;
    let startPoint: { x: number; y: number } | null = null;
    let box: L.Rectangle | null = null;

    map.dragging.disable();
    container.classList.add("is-drawing");

    const latLngOf = (event: PointerEvent) => map.mouseEventToLatLng(event);

    const down = (event: PointerEvent) => {
      if (event.button !== 0 && event.pointerType === "mouse") {
        return;
      }
      container.setPointerCapture?.(event.pointerId);
      start = latLngOf(event);
      startPoint = { x: event.clientX, y: event.clientY };
    };
    const move = (event: PointerEvent) => {
      if (!start) {
        return;
      }
      const bounds = L.latLngBounds(start, latLngOf(event));
      if (box) {
        box.setBounds(bounds);
      } else {
        box = L.rectangle(bounds, {
          color: COLORS.selected,
          weight: 2,
          dashArray: "6 4",
          fillOpacity: 0.1,
          interactive: false,
        }).addTo(map);
      }
    };
    const up = (event: PointerEvent) => {
      if (!start || !startPoint) {
        return;
      }
      const end = latLngOf(event);
      const moved = Math.hypot(event.clientX - startPoint.x, event.clientY - startPoint.y);
      const from = start;
      start = null;
      box?.remove();
      box = null;
      if (moved < MIN_DRAW_PX) {
        return;
      }
      const round = (value: number) => Number(value.toFixed(5));
      const west = wrapLongitude(Math.min(from.lng, end.lng));
      const east = wrapLongitude(Math.max(from.lng, end.lng));
      onDrawRef.current?.({
        west: round(Math.min(west, east)),
        east: round(Math.max(west, east)),
        south: round(Math.max(-90, Math.min(from.lat, end.lat))),
        north: round(Math.min(90, Math.max(from.lat, end.lat))),
      });
      setDrawing(false);
    };

    container.addEventListener("pointerdown", down);
    container.addEventListener("pointermove", move);
    container.addEventListener("pointerup", up);
    container.addEventListener("pointercancel", up);
    return () => {
      container.removeEventListener("pointerdown", down);
      container.removeEventListener("pointermove", move);
      container.removeEventListener("pointerup", up);
      container.removeEventListener("pointercancel", up);
      container.classList.remove("is-drawing");
      box?.remove();
      map.dragging.enable();
    };
  }, [drawing, onEarth]);

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
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (map && onEarth) return addEarthBasemap(map, locale, basemap, setBasemapUnavailable);
  }, [basemap, locale, onEarth]);

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

        {onEarth && onDrawBbox && (
          <button
            type="button"
            className={`map-draw${drawing ? " is-active" : ""}`}
            aria-pressed={drawing}
            onClick={() => setDrawing((value) => !value)}
          >
            {drawing ? t("map.drawStop") : t("map.drawStart")}
          </button>
        )}

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

      {onEarth && drawing && (
        <p className="map-status" role="status">
          {t("map.drawHint")}
        </p>
      )}

      {!onEarth && onDrawBbox && (
        <p className="map-status" role="status">
          {t("map.planetAnalysisLimit")}
        </p>
      )}

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
