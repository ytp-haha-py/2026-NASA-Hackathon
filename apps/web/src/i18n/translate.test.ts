import { describe, expect, it } from "vitest";
import { en, enLabels } from "../locales/en/messages";
import { zhTW } from "../locales/zh-TW/messages";
import { createI18n } from "./I18nProvider";
import {
  CATALOGS,
  interpolate,
  matchLocale,
  pluralKey,
  resolveBrowserLocale,
  type Catalog,
} from "./translate";

function placeholders(template: string): string[] {
  return [...template.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
}

describe("locale detection", () => {
  it.each([
    ["zh-TW", "zh-TW"],
    ["zh-HK", "zh-TW"],
    ["zh-MO", "zh-TW"],
    ["zh", "zh-TW"],
    ["zh-Hant", "zh-TW"],
    ["zh-Hant-TW", "zh-TW"],
    ["zh_TW", "zh-TW"],
    ["en", "en"],
    ["en-GB", "en"],
  ])("maps %s to %s", (tag, expected) => {
    expect(matchLocale(tag)).toBe(expected);
  });

  it.each(["zh-CN", "zh-SG", "zh-Hans-CN", "fr-FR", "ja", ""])(
    "does not claim support for %s",
    (tag) => {
      expect(matchLocale(tag)).toBeNull();
    },
  );

  it("uses the first supported browser language and falls back to English", () => {
    expect(resolveBrowserLocale(["zh-HK", "en-US"])).toBe("zh-TW");
    expect(resolveBrowserLocale(["en-US", "zh-TW"])).toBe("en");
    expect(resolveBrowserLocale(["fr-FR", "zh-TW"])).toBe("zh-TW");
    expect(resolveBrowserLocale(["fr-FR", "de-DE"])).toBe("en");
    expect(resolveBrowserLocale(["zh-CN"])).toBe("en");
    expect(resolveBrowserLocale([])).toBe("en");
  });
});

describe("catalogs", () => {
  const enKeys = Object.keys(en) as (keyof typeof en)[];

  it("translates every English key to zh-TW (plural _one variants excepted)", () => {
    const missing = enKeys.filter((key) => {
      if (zhTW[key] !== undefined) {
        return false;
      }
      // Chinese has a single plural form; `_other` covers `_one`.
      return !(key.endsWith("_one") && zhTW[key.replace(/_one$/, "_other") as keyof typeof en]);
    });
    expect(missing).toEqual([]);
  });

  it("keeps the same placeholders in zh-TW as in English", () => {
    const mismatched = enKeys.filter((key) => {
      const translated = zhTW[key];
      return translated !== undefined && placeholders(translated).join() !== placeholders(en[key]).join();
    });
    expect(mismatched).toEqual([]);
  });

  it("keeps mandatory scientific caveats in the zh-TW catalog", () => {
    expect(zhTW["results.notGlobalTitle"]).toContain("不是全球");
    expect(zhTW["app.idleItemDistance"]).toContain("不是百分比");
    expect(zhTW["location.contextHeading"]).toMatch(/不是.*礦物學/);
    expect(zhTW["location.limitWorldCover"]).toContain("月球");
    expect(zhTW["search.lolaCaveat"]).toContain("90%");
  });
});

describe("translator", () => {
  it("interpolates known placeholders and leaves unknown ones literal", () => {
    expect(interpolate("GET /locations/{id} for {name}", { name: "A" })).toBe(
      "GET /locations/{id} for A",
    );
  });

  it("falls back to English for keys missing from the active catalog", () => {
    const partial: Catalog = {
      messages: { "app.title": "行星類比地形探索器" },
      labels: enLabels,
    };
    const i18n = createI18n("zh-TW", () => undefined, partial);
    expect(i18n.t("app.title")).toBe("行星類比地形探索器");
    expect(i18n.t("app.tabSearch")).toBe("Search target");
  });

  it("selects English plural forms and the single zh-TW form", () => {
    expect(pluralKey("app.coverageHeading", 1, "en", CATALOGS.en)).toBe(
      "app.coverageHeading_one",
    );
    expect(pluralKey("app.coverageHeading", 0, "en", CATALOGS.en)).toBe(
      "app.coverageHeading_other",
    );
    expect(pluralKey("app.coverageHeading", 1, "zh-TW", CATALOGS["zh-TW"])).toBe(
      "app.coverageHeading_other",
    );
    const zh = createI18n("zh-TW");
    expect(zh.tp("app.coverageHeading", 1)).toBe("1 個可搜尋的地球區域");
  });

  it("maps stable identifiers in zh-TW and keeps unknown identifiers unchanged", () => {
    const zh = createI18n("zh-TW");
    expect(zh.label.feature("slope_mean_deg", "Mean slope")).toBe("平均坡度");
    expect(zh.label.feature("future_feature", "Future feature")).toBe("Future feature");
    expect(zh.label.status("verified")).toBe("已驗證");
    expect(zh.label.status("brand_new_status")).toBe("brand_new_status");
    expect(zh.label.errorCode("aoi_too_large")).toBe("AOI 範圍過大");
    expect(zh.label.errorCode("never_seen_code")).toBeNull();

    const english = createI18n("en");
    expect(english.label.feature("slope_mean_deg", "Mean slope")).toBe("Mean slope");
    expect(english.label.status("verified")).toBe("verified");
    expect(english.label.errorCode("aoi_too_large")).toBeNull();
  });

  it("formats numbers per locale without changing distance or bbox numerals", () => {
    const zh = createI18n("zh-TW");
    expect(zh.fmt.number(1980)).toBe("1,980");
    expect(zh.fmt.percent(0.5)).toBe("50.0%");
    expect(zh.fmt.distance(0.03700001)).toBe("0.037");
    expect(zh.fmt.bbox({ west: -13.645, south: 29.035, east: -13.635, north: 29.045 })).toBe(
      "緯度 29.035° 至 29.045°，經度 -13.645° 至 -13.635°",
    );
    expect(zh.fmt.list(["月球", "火星"])).toBe("月球和火星");
    expect(createI18n("en").fmt.list(["Moon", "Mars"])).toBe("Moon and Mars");
  });

  it("localizes exclusion reasons using feature ids", () => {
    const zh = createI18n("zh-TW");
    expect(
      zh.fmt.exclusionReason("missing_metric:slope_p90_deg", new Map()),
    ).toBe("缺少必要指標：第 90 百分位坡度");
    expect(
      createI18n("en").fmt.exclusionReason(
        "missing_metric:slope_p90_deg",
        new Map([["slope_p90_deg", "90th percentile slope"]]),
      ),
    ).toBe("Missing required metric: 90th percentile slope");
  });
});
