import L from "leaflet";
import type { ExpressionSpecification, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import type { Locale } from "../i18n/translate";

export type EarthBasemap = "map" | "terrain" | "satellite";

const STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
const ARCGIS = "https://server.arcgisonline.com/ArcGIS/rest/services";
const VECTOR_ATTRIBUTION =
  '<a href="https://openfreemap.org/">OpenFreeMap</a> © <a href="https://openmaptiles.org/">OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>';
const IMAGERY = {
  map: {
    url: `${ARCGIS}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`,
    maxNativeZoom: 16,
    attribution: "Tiles © Esri, HERE, Garmin, OpenStreetMap contributors, and the GIS user community",
  },
  terrain: {
    url: `${ARCGIS}/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}`,
    maxNativeZoom: 16,
    attribution: "Tiles © Esri and the World Hillshade contributors",
  },
  satellite: {
    url: `${ARCGIS}/World_Imagery/MapServer/tile/{z}/{y}/{x}`,
    maxNativeZoom: 18,
    attribution: "Tiles © Esri, Maxar, Earthstar Geographics, and the GIS user community",
  },
};

const ENGLISH_NAME: ExpressionSpecification = [
  "coalesce",
  ["get", "name:en"],
  ["get", "name_en"],
  ["get", "name:latin"],
  "",
];

export function localizeEarthStyle(
  style: StyleSpecification,
  locale: Locale,
  basemap: EarthBasemap,
): StyleSpecification {
  const name: ExpressionSpecification =
    locale === "zh-TW"
      ? ["coalesce", ["get", "name:zh-Hant"], ["get", "name:zh"], ENGLISH_NAME]
      : ENGLISH_NAME;
  const layers = style.layers.map((layer) => {
    // Replace place-name expressions while keeping numeric road shields and other symbols.
    if (
      layer.type !== "symbol" ||
      !/["{]name[":_}]/.test(JSON.stringify(layer.layout?.["text-field"] ?? ""))
    ) {
      return layer;
    }
    return { ...layer, layout: { ...layer.layout, "text-field": name } };
  });
  if (basemap === "map") {
    return { ...style, layers };
  }

  const imagery = IMAGERY[basemap];
  return {
    ...style,
    sources: {
      ...style.sources,
      imagery: {
        type: "raster",
        tiles: [imagery.url],
        tileSize: 256,
        maxzoom: imagery.maxNativeZoom,
        attribution: imagery.attribution,
      },
    },
    layers: [
      { id: "earth-imagery", type: "raster", source: "imagery" },
      ...layers.filter((layer) => layer.type === "symbol"),
    ],
  };
}

export function addEarthBasemap(
  map: L.Map,
  locale: Locale,
  basemap: EarthBasemap = "map",
  setUnavailable?: (unavailable: boolean) => void,
): () => void {
  const maxZoom = basemap === "map" ? 19 : 18;
  map.setMaxZoom(maxZoom);
  let layer: L.Layer | undefined;
  const request = new AbortController();

  function addRaster() {
    const { url, ...options } = IMAGERY[basemap];
    layer = L.tileLayer(url, { ...options, maxZoom })
      .on("tileerror", () => setUnavailable?.(true))
      .on("tileload", () => setUnavailable?.(false))
      .addTo(map);
  }

  async function addVector() {
    const [response, { maplibreGL }] = await Promise.all([
      fetch(STYLE_URL, { signal: request.signal }),
      import("@maplibre/maplibre-gl-leaflet"),
    ]);
    if (!response.ok) throw new Error(`Basemap style returned HTTP ${response.status}`);
    const style: StyleSpecification = await response.json();
    if (request.signal.aborted) return;

    const vector = maplibreGL({
      style: localizeEarthStyle(style, locale, basemap),
      interactive: false,
      // Keep attribution stable when Leaflet replaces the layer on a language change.
      attributionControl: {
        customAttribution: basemap === "map"
          ? VECTOR_ATTRIBUTION
          : `${VECTOR_ATTRIBUTION}, ${IMAGERY[basemap].attribution}`,
      },
      localIdeographFontFamily: '"Microsoft JhengHei", "PingFang TC", "Noto Sans CJK TC", sans-serif',
    });
    try {
      vector.addTo(map);
    } catch (error) {
      // The Leaflet adapter's cleanup assumes its renderer was initialized.
      if (!vector.getMaplibreMap()) {
        vector.onRemove = () => {
          vector.getContainer()?.remove();
          return vector;
        };
      }
      vector.remove();
      throw error;
    }
    layer = vector;
    const renderer = vector.getMaplibreMap();
    renderer.on("error", () => setUnavailable?.(true));
    renderer.on("load", () => setUnavailable?.(false));
  }

  if (window.WebGLRenderingContext || window.WebGL2RenderingContext) {
    void addVector().catch(() => {
      if (!request.signal.aborted) addRaster();
    });
  } else {
    addRaster();
  }

  return () => {
    request.abort();
    layer?.remove();
  };
}
