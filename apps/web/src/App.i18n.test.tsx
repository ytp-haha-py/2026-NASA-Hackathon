import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { LOCALE_STORAGE_KEY } from "./i18n/translate";
import {
  analyzeQueuedFixture,
  aoiLocationFixture,
  jobPartialFixture,
  referenceFixture,
} from "./test/fixtures";
import {
  errorResponse,
  installMockFetch,
  jsonResponse,
  searchRequestFromCalls,
  type FetchHandlers,
} from "./test/mock-api";
import { applyExampleTarget } from "./test/example-target";

vi.mock("./components/MapPanel", () => ({
  MapPanel: ({ results }: { results: unknown[] }) => (
    <div data-testid="map-stub">{results.length}</div>
  ),
}));

const EN_HEADING = "Describe a terrain-shape target";
const ZH_HEADING = "描述地形形狀目標";

function stubBrowserLanguages(languages: string[]) {
  vi.spyOn(window.navigator, "languages", "get").mockReturnValue(languages);
  vi.spyOn(window.navigator, "language", "get").mockReturnValue(languages[0] ?? "");
}

async function renderApp(
  handlers: FetchHandlers = {},
  props: Parameters<typeof App>[0] = {},
) {
  const fetchMock = installMockFetch(handlers);
  const view = render(<App aoiPollIntervalMs={10} {...props} />);
  await screen.findByRole("tablist");
  await applyExampleTarget();
  return { fetchMock, view };
}

async function switchTo(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(screen.getByRole("button", { name }));
}

describe("initial locale", () => {
  it.each([["zh-TW"], ["zh-HK"], ["zh"]])(
    "uses Traditional Chinese for browser language %s",
    async (language) => {
      stubBrowserLanguages([language, "en-US"]);
      await renderApp();
      expect(screen.getByRole("heading", { name: ZH_HEADING })).toBeInTheDocument();
      expect(document.documentElement.lang).toBe("zh-TW");
    },
  );

  it("falls back to English for unsupported browser languages", async () => {
    stubBrowserLanguages(["fr-FR", "de-DE"]);
    await renderApp();
    expect(screen.getByRole("heading", { name: EN_HEADING })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("en");
  });

  it("prefers a saved explicit choice over the browser language", async () => {
    stubBrowserLanguages(["zh-TW"]);
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "en");
    await renderApp();
    expect(screen.getByRole("heading", { name: EN_HEADING })).toBeInTheDocument();
  });

  it("ignores an invalid saved value", async () => {
    stubBrowserLanguages(["zh-TW"]);
    window.localStorage.setItem(LOCALE_STORAGE_KEY, "klingon");
    await renderApp();
    expect(screen.getByRole("heading", { name: ZH_HEADING })).toBeInTheDocument();
  });
});

describe("language switcher", () => {
  it("is an accessible group whose pressed state tracks the active language", async () => {
    await renderApp();
    const group = screen.getByRole("group", { name: "Language" });
    const zhButton = within(group).getByRole("button", { name: "繁體中文" });
    const enButton = within(group).getByRole("button", { name: "English" });
    expect(zhButton).toHaveAttribute("lang", "zh-TW");
    expect(enButton).toHaveAttribute("aria-pressed", "true");

    await userEvent.setup().click(zhButton);

    expect(screen.getByRole("group", { name: "語言" })).toBeInTheDocument();
    expect(zhButton).toHaveAttribute("aria-pressed", "true");
    expect(enButton).toHaveAttribute("aria-pressed", "false");
  });

  it("switches immediately, updates <html lang>, and persists the choice", async () => {
    const user = userEvent.setup();
    const { view } = await renderApp();

    await switchTo(user, "繁體中文");
    expect(screen.getByRole("heading", { name: ZH_HEADING })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe("zh-TW");
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("zh-TW");

    view.unmount();
    await renderApp();
    expect(screen.getByRole("heading", { name: ZH_HEADING })).toBeInTheDocument();

    await switchTo(user, "English");
    expect(screen.getByRole("heading", { name: EN_HEADING })).toBeInTheDocument();
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("en");
  });

  it("keeps form inputs and validation errors, re-rendered in the new language", async () => {
    const user = userEvent.setup();
    const { fetchMock } = await renderApp();

    const slopeGroup = screen.getByRole("group", { name: "Mean slope" });
    const slopeValue = within(slopeGroup).getByLabelText("Target value (degree)");
    await user.clear(slopeValue);
    await user.type(slopeValue, "95");
    await user.click(screen.getByRole("button", { name: "Search indexed Earth candidates" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Mean slope value: Enter a value between 0 and 90 degree.",
    );

    await switchTo(user, "繁體中文");

    const zhGroup = screen.getByRole("group", { name: "平均坡度" });
    expect(within(zhGroup).getByLabelText("目標值（度）")).toHaveValue(95);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "平均坡度（目標值）：請輸入介於 0 與 90 度 之間的數值。",
    );
    expect(searchRequestFromCalls(fetchMock)).toBeNull();
  });

  it("keeps search results and the selected result across a switch", async () => {
    const user = userEvent.setup();
    await renderApp();

    await user.click(screen.getByRole("button", { name: "Search indexed Earth candidates" }));
    await user.click(
      await screen.findByRole("button", { name: "Select rank 1: Observed test window" }),
    );
    await screen.findByRole("heading", { name: "Selected Earth candidate" });

    await switchTo(user, "繁體中文");

    expect(screen.getByTestId("coverage-headline")).toHaveTextContent(
      "在 4 個已索引區域中的前 2 名",
    );
    expect(
      screen.getByRole("button", { name: "選擇第 1 名：Observed test window" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: "已選取的地球候選區域" })).toBeInTheDocument();
  });
});

describe("Traditional Chinese workflows", () => {
  it("runs a manual search with localized caveats and an unchanged API payload", async () => {
    const user = userEvent.setup();
    const { fetchMock } = await renderApp({}, { initialLocale: "zh-TW" });

    expect(screen.getByText(/假設性範例，不是 NASA 量測值/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "搜尋已索引的地球候選區域" }));

    await screen.findByTestId("coverage-headline");
    expect(searchRequestFromCalls(fetchMock)).toEqual({
      body: "custom",
      reference_id: null,
      target: {
        slope_mean_deg: { value: 12, weight: 1 },
        terrain_relief_p95_p05_m: { value: 80, weight: 0.5 },
      },
      filters: {},
      limit: 10,
    });
    expect(screen.getByText("這不是全球前 10 名。")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "回傳的 2 筆紀錄中有 1 筆是「示範／合成」資料。",
    );
    expect(screen.getByText(/缺少必要指標：第 90 百分位坡度/)).toBeInTheDocument();
    expect(screen.getByText("1 個已排除的候選區域 — 顯示原因")).toBeInTheDocument();

    const observedCard = screen.getByTestId("result-earth-observed-1");
    expect(within(observedCard).getByText("0.037")).toBeInTheDocument();
    expect(within(observedCard).getByText("12.4 度")).toBeInTheDocument();
    expect(within(observedCard).getByText("距離")).toBeInTheDocument();

    // API-provided English caveats stay verbatim and are marked as English.
    const caveat = within(observedCard).getByText(/Earth land-cover context is informational only/);
    expect(caveat).toHaveAttribute("lang", "en");
  });

  it("shows the 409 error localized while keeping the server code and message", async () => {
    const user = userEvent.setup();
    await renderApp(
      {
        search: () =>
          errorResponse(409, "no_indexed_data", "The Earth candidate index is empty."),
      },
      { initialLocale: "zh-TW" },
    );

    await user.click(screen.getByRole("button", { name: "搜尋已索引的地球候選區域" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("沒有已索引的地球資料（HTTP 409）");
    expect(alert).toHaveTextContent("錯誤代碼：no_indexed_data（沒有已索引的資料） · HTTP 409");
    expect(within(alert).getByText("The Earth candidate index is empty.")).toHaveAttribute(
      "lang",
      "en",
    );
  });

  it("runs a Moon LOLA reference search with the localized caveat", async () => {
    const user = userEvent.setup();
    const { fetchMock } = await renderApp(
      { references: () => jsonResponse([referenceFixture]) },
      { initialLocale: "zh-TW" },
    );

    expect(screen.getByText(/火星預設無法使用/)).toBeInTheDocument();

    await user.click(screen.getByRole("radio", { name: /^月球/ }));
    expect(screen.getByRole("option", { name: "Shackleton Rim (LOLA Site04 ROI 2)" }))
      .toBeInTheDocument();
    expect(screen.getByText("LOLA 參考資料注意事項：")).toBeInTheDocument();
    expect(screen.getByText(/約有 90% 為內插值/)).toBeInTheDocument();
    expect(screen.getByText(/不是地球 WGS84/)).toBeInTheDocument();

    const slopeWeight = within(screen.getByRole("group", { name: "平均坡度" })).getByLabelText(
      "權重（≥ 0）",
    );
    await user.clear(slopeWeight);
    await user.type(slopeWeight, "1");

    await user.click(screen.getByRole("button", { name: "搜尋已索引的地球候選區域" }));
    await screen.findByTestId("coverage-headline");
    expect(searchRequestFromCalls(fetchMock)).toMatchObject({
      body: "Moon",
      reference_id: "moon-shackleton-rim-site04-roi2",
      weights: { slope_mean_deg: 1 },
    });
  });

  it("shows details, land-cover context and the export controls in zh-TW", async () => {
    const user = userEvent.setup();
    await renderApp({}, { initialLocale: "zh-TW" });

    await user.click(screen.getByRole("button", { name: "搜尋已索引的地球候選區域" }));
    await user.click(
      await screen.findByRole("button", { name: "選擇第 1 名：Observed test window" }),
    );

    const contextHeading = await screen.findByRole("heading", {
      name: /地球土地覆蓋背景資訊（ESA WorldCover 2021）/,
    });
    expect(contextHeading).toHaveTextContent(/不是」礦物學／地質學資料/);
    const contextScope = within(contextHeading.closest("section") as HTMLElement);
    expect(contextScope.getByText("土地覆蓋：裸地／稀疏植被比例")).toBeInTheDocument();
    expect(contextScope.getByText("50.0%")).toBeInTheDocument();

    expect(await screen.findByRole("button", { name: "下載 JSON" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "複製 JSON" })).toBeInTheDocument();
    expect(screen.getByText("本應用程式不是聊天機器人")).toBeInTheDocument();
    // Machine-readable export keys are never localized.
    expect(screen.getByText(/"export_version"/)).toBeInTheDocument();
  });

  it("validates and monitors an AOI job in zh-TW with per-provider status", async () => {
    const user = userEvent.setup();
    await renderApp(
      {
        analyze: () => jsonResponse(analyzeQueuedFixture, 202),
        job: () => jsonResponse(jobPartialFixture),
        areaFeatures: () => jsonResponse({ ...aoiLocationFixture, context_observations: [] }),
      },
      { initialLocale: "zh-TW" },
    );
    await user.click(screen.getByRole("button", { name: "分析新區域" }));

    const east = screen.getByLabelText("東側經度（°）");
    await user.clear(east);
    await user.type(east, "-13.6449");
    await user.click(screen.getByRole("button", { name: "分析此地球 AOI" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/AOI 太小：每邊至少要 600 m/);

    await user.clear(east);
    await user.type(east, "-13.635");
    await user.click(screen.getByRole("button", { name: "分析此地球 AOI" }));

    expect(await screen.findByText("部分結果")).toBeInTheDocument();
    const jobPanel = screen.getByTestId("aoi-job-panel");
    expect(within(jobPanel).getByText("部分完成")).toHaveAttribute("data-status", "partial");
    expect(within(jobPanel).getByText("失敗")).toHaveAttribute("data-status", "failed");
    expect(within(jobPanel).getByText("worldcover_context_provider_failed")).toBeInTheDocument();
    expect(within(jobPanel).getByText(/ESA WorldCover window read failed in test/))
      .toHaveAttribute("lang", "en");
    expect(screen.getByTestId("aoi-record")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "以 AOI 特徵作為搜尋目標" })).toBeEnabled();
  });

  it("keeps an in-progress AOI job state when switching language", async () => {
    const user = userEvent.setup();
    await renderApp({
      analyze: () => jsonResponse(analyzeQueuedFixture, 202),
      job: () => jsonResponse(jobPartialFixture),
      areaFeatures: () => jsonResponse(aoiLocationFixture),
    });
    await user.click(screen.getByRole("button", { name: "Analyze a new area" }));
    await user.click(screen.getByRole("button", { name: "Analyze this Earth AOI" }));
    await screen.findByTestId("aoi-record");

    await switchTo(user, "繁體中文");

    expect(screen.getByRole("button", { name: "分析新區域" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("部分結果")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("aoi-record")).toBeInTheDocument());
  });
});
