import { useCallback, useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { GlobalCoverageOut, GlobalSearchResultOut } from "../api/client";
import { useI18n } from "../i18n/I18nProvider";

type GlobalMapProps = {
  coverage: GlobalCoverageOut | null;
  results: GlobalSearchResultOut[];
  selectedId: string | null;
  onSelect: (candidateId: string) => void;
};

const COLORS = {
  coverage: "#1d4ed8",
  candidate: "#b93d0a",
};

function coverageBounds(coverage: GlobalCoverageOut | null): L.LatLngBounds | null {
  const bbox = coverage?.bbox;
  if (!bbox) {
    return null;
  }
  const { west, south, east, north } = bbox;
  if (![west, south, east, north].every((value) => Number.isFinite(value))) {
    return null;
  }
  if (east <= west || north <= south) {
    return null;
  }
  return L.latLngBounds([south, west], [north, east]);
}

function candidateBounds(results: GlobalSearchResultOut[]): L.LatLngBounds {
  const bounds = L.latLngBounds([]);
  for (const result of results) {
    bounds.extend([result.bbox.south, result.bbox.west]);
    bounds.extend([result.bbox.north, result.bbox.east]);
  }
  return bounds;
}

function rankIcon(rank: number, selected: boolean): L.DivIcon {
  const size = selected ? 30 : 24;
  const pin = document.createElement("span");
  pin.className = `rank-pin is-coarse${selected ? " is-selected" : ""}`;
  pin.textContent = String(rank);
  return L.divIcon({
    className: "rank-pin-icon",
    html: pin,
    iconSize: [size, size],
    iconAnchor: [size / 2, size + 2],
    tooltipAnchor: [0, -size],
  });
}

export function GlobalMap({ coverage, results, selectedId, onSelect }: GlobalMapProps) {
  const { t, fmt } = useI18n();
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const layerRef = useRef<L.LayerGroup | null>(null);
  const onSelectRef = useRef(onSelect);

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
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    const layer = L.layerGroup().addTo(map);
    mapRef.current = map;
    layerRef.current = layer;
    const timer = window.setTimeout(() => map.invalidateSize(), 0);
    return () => {
      window.clearTimeout(timer);
      map.remove();
      mapRef.current = null;
      layerRef.current = null;
    };
  }, []);

  const showAll = useCallback(() => {
    const map = mapRef.current;
    if (!map) {
      return;
    }
    const candidates = candidateBounds(results);
    if (candidates.isValid()) {
      map.fitBounds(candidates, { padding: [24, 24], maxZoom: 9 });
      return;
    }
    const extent = coverageBounds(coverage);
    if (extent) {
      map.fitBounds(extent, { padding: [24, 24], maxZoom: 9 });
      return;
    }
    map.setView([20, 0], 2);
  }, [coverage, results]);

  useEffect(() => {
    showAll();
  }, [showAll]);

  useEffect(() => {
    const map = mapRef.current;
    const layer = layerRef.current;
    if (!map || !layer) {
      return;
    }
    layer.clearLayers();

    const coverageBox = coverageBounds(coverage);
    if (coverageBox) {
      L.rectangle(coverageBox, {
        color: COLORS.coverage,
        weight: 2,
        dashArray: "6 4",
        fillOpacity: 0.03,
      })
        .bindTooltip(t("global.coverageHeading"))
        .addTo(layer);
    }

    for (const result of results) {
      const selected = result.candidate_id === selectedId;
      const color = selected ? COLORS.coverage : COLORS.candidate;
      L.rectangle(
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
      )
        .on("click", () => onSelectRef.current(result.candidate_id))
        .addTo(layer);

      const center = [
        (result.bbox.south + result.bbox.north) / 2,
        (result.bbox.west + result.bbox.east) / 2,
      ] as [number, number];
      const marker = L.marker(center, {
        icon: rankIcon(result.rank, selected),
        zIndexOffset: selected ? 1000 : 0,
        keyboard: false,
      });
      marker.bindTooltip(
        `#${result.rank} ${result.candidate_id} · ${t("global.colDistance")} ${fmt.distance(result.distance)}`,
      );
      marker.on("click", () => onSelectRef.current(result.candidate_id));
      marker.addTo(layer);
    }
  }, [coverage, results, selectedId, t, fmt]);

  useEffect(() => {
    const map = mapRef.current;
    const selected = results.find((result) => result.candidate_id === selectedId);
    if (!map || !selected) {
      return;
    }
    map.flyToBounds(
      L.latLngBounds(
        [selected.bbox.south, selected.bbox.west],
        [selected.bbox.north, selected.bbox.east],
      ),
      { padding: [24, 24], maxZoom: 13, duration: 0.6 },
    );
  }, [selectedId, results]);

  return (
    <section className="global-map-panel" aria-label={t("global.mapRegion")}>
      <div className="map-toolbar">
        <button type="button" className="secondary-button" onClick={showAll}>
          {t("global.showAll")}
        </button>
      </div>
      <div className="global-map" ref={containerRef} data-testid="global-map" />
      <p className="field-hint">{t("global.mapNote")}</p>
    </section>
  );
}
