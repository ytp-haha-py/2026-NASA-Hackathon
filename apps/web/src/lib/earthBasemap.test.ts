import type { StyleSpecification } from "maplibre-gl";
import { describe, expect, it } from "vitest";
import { localizeEarthStyle } from "./earthBasemap";

const style: StyleSpecification = {
  version: 8,
  sources: { places: { type: "vector", tiles: ["https://example.com/{z}/{x}/{y}.pbf"] } },
  layers: [
    { id: "background", type: "background" },
    {
      id: "cities",
      type: "symbol",
      source: "places",
      "source-layer": "place",
      layout: { "text-field": ["concat", ["get", "name:latin"], " ", ["get", "name:nonlatin"]] },
    },
    {
      id: "road-number",
      type: "symbol",
      source: "places",
      "source-layer": "transportation_name",
      layout: { "text-field": ["get", "ref"] },
    },
  ],
};

describe("localizeEarthStyle", () => {
  it("selects localized names and never falls back to raw local-language names", () => {
    const english = ["coalesce", ["get", "name:en"], ["get", "name_en"], ["get", "name:latin"], ""];
    const en = localizeEarthStyle(style, "en", "map");
    const zh = localizeEarthStyle(style, "zh-TW", "map");
    expect(en.layers[1]).toMatchObject({ layout: { "text-field": english } });
    expect(zh.layers[1]).toMatchObject({
      layout: { "text-field": ["coalesce", ["get", "name:zh-Hant"], ["get", "name:zh"], english] },
    });
    expect(en.layers[2]).toBe(style.layers[2]);
  });

  it.each(["terrain", "satellite"] as const)("places the same labels over %s imagery", (mode) => {
    const localized = localizeEarthStyle(style, "en", mode);
    expect(localized.layers.map((layer) => layer.id)).toEqual(["earth-imagery", "cities", "road-number"]);
    expect(localized.layers[1]).toEqual(localizeEarthStyle(style, "en", "map").layers[1]);
  });
});
