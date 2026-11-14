import type {
  AoiAnalyzeResponse,
  CapabilitiesOut,
  ContextFeatureOut,
  ContextObservationOut,
  ContextOut,
  DatasetOut,
  FeatureOut,
  JobOut,
  LocationOut,
  ReferenceOut,
  SearchResponse,
  SearchResultOut,
} from "../api/client";

const featureContext = {
  dimension_id: "terrain_shape",
  value_kind: "numeric",
  score_role: "similarity",
  spatial_support: "fixed 1.98 km x 1.98 km window; 22 x 22 cells at 90 m",
  temporal_support: "static epoch of the source DEM release",
  provider_coverage_by_body: {
    Earth: "verified_where_indexed",
    Moon: "verified_only_for_processed_reference_rois",
    Mars: "unavailable",
  },
  comparability_gate:
    "Same method_version, projected metric CRS, 90 m analysis grid and valid-data rule. Terrain-shape comparability does not imply environmental equivalence.",
} as const;

export const featuresFixture: FeatureOut[] = [
  {
    ...featureContext,
    id: "slope_mean_deg",
    label: "Mean slope",
    unit: "degree",
    description: "Mean terrain gradient angle over the analysis window.",
    method:
      "DEM resampled (bilinear) to 90 m in a metric CRS; per-cell gradient magnitude from a 3x3 Horn operator; arithmetic mean of valid cells.",
    method_version: "terrain-metrics-1.0.0",
    min_value: 0,
    max_value: 90,
    scale: 10,
    scale_rationale:
      "Fixed a-priori contrast scale: 10 degrees separates smooth plains from rugged hillslopes at 90 m sampling.",
    extraction_scale_m: 90,
    valid_domain: "0 <= value <= 90 degrees",
    missing_policy:
      "Candidates missing this metric are excluded from a search that requests it.",
  },
  {
    ...featureContext,
    id: "slope_p90_deg",
    label: "90th percentile slope",
    unit: "degree",
    description: "90th percentile of per-cell terrain gradient angle in the analysis window.",
    method:
      "Same gradient grid as slope_mean_deg; linear-interpolated 90th percentile over valid cells.",
    method_version: "terrain-metrics-1.0.0",
    min_value: 0,
    max_value: 90,
    scale: 15,
    scale_rationale: "Fixed a-priori contrast scale of 15 degrees for steep-tail terrain.",
    extraction_scale_m: 90,
    valid_domain: "0 <= value <= 90 degrees",
    missing_policy:
      "Candidates missing this metric are excluded from a search that requests it.",
  },
  {
    ...featureContext,
    id: "terrain_relief_p95_p05_m",
    label: "Terrain relief (p95-p05)",
    unit: "meter",
    description:
      "Difference between the 95th and 5th percentile elevation inside the analysis window.",
    method:
      "DEM resampled (bilinear) to 90 m; linear-interpolated percentiles over valid cells; p95 minus p05.",
    method_version: "terrain-metrics-1.0.0",
    min_value: 0,
    max_value: 20000,
    scale: 200,
    scale_rationale:
      "Fixed a-priori contrast scale: 200 m of relief separates subdued from strongly dissected terrain.",
    extraction_scale_m: 90,
    valid_domain: "0 <= value <= 20000 meters",
    missing_policy:
      "Candidates missing this metric are excluded from a search that requests it.",
  },
  {
    ...featureContext,
    id: "roughness_std_m",
    label: "Detrended roughness (std dev)",
    unit: "meter",
    description:
      "Standard deviation of elevation residuals after removing a least-squares plane fitted across the analysis window.",
    method:
      "DEM resampled (bilinear) to 90 m in a metric CRS; least-squares plane z = a*x + b*y + c fitted on valid cells; population standard deviation of residuals.",
    method_version: "terrain-metrics-1.0.0",
    min_value: 0,
    max_value: 5000,
    scale: 20,
    scale_rationale: "Fixed a-priori contrast scale of 20 m of detrended residual height.",
    extraction_scale_m: 90,
    valid_domain: "0 <= value <= 5000 meters",
    missing_policy:
      "Candidates missing this metric are excluded from a search that requests it.",
  },
];

const WORLDCOVER_CLASSES: {
  code: number;
  slug: string;
  label: string;
}[] = [
  { code: 10, slug: "tree_cover", label: "Tree cover" },
  { code: 20, slug: "shrubland", label: "Shrubland" },
  { code: 30, slug: "grassland", label: "Grassland" },
  { code: 40, slug: "cropland", label: "Cropland" },
  { code: 50, slug: "built_up", label: "Built-up" },
  { code: 60, slug: "bare_sparse", label: "Bare / sparse vegetation" },
  { code: 70, slug: "snow_ice", label: "Snow and ice" },
  { code: 80, slug: "water", label: "Permanent water bodies" },
  { code: 90, slug: "herbaceous_wetland", label: "Herbaceous wetland" },
  { code: 95, slug: "mangrove", label: "Mangroves" },
  { code: 100, slug: "moss_lichen", label: "Moss and lichen" },
];

export const contextFeaturesFixture: ContextFeatureOut[] = WORLDCOVER_CLASSES.map(
  (coverClass) => ({
    feature_id: `landcover_${coverClass.slug}_fraction`,
    label: `Land cover: ${coverClass.label} fraction`,
    class_code: coverClass.code,
    dimension_id: "land_cover_context",
    description: `Fraction of valid ESA WorldCover pixels mapped to class ${coverClass.code} (${coverClass.label}). Earth land-cover context only.`,
    value_kind: "distribution",
    unit: "fraction",
    method_version: "worldcover-2021-v200-1.0.0",
    spatial_support:
      "AOI footprint at native 10 m WorldCover pixels; fractions over valid pixels",
    temporal_support: "ESA WorldCover 2021 v200 static annual epoch",
    provider_coverage_by_body: {
      Earth: "verified_where_indexed",
      Moon: "not_applicable",
      Mars: "not_applicable",
    },
    compare_strategy: "context_only_filter",
    score_role: "context_only",
    quality_rules:
      "valid_fraction >= 0.8; class fractions sum to 1 within tolerance; unknown stays unknown",
    reference: "https://esa-worldcover.org/en/data-access",
  }),
);

const CONTEXT_VALUES: Record<string, number> = {
  landcover_tree_cover_fraction: 0.05,
  landcover_shrubland_fraction: 0.12,
  landcover_grassland_fraction: 0.25,
  landcover_cropland_fraction: 0.03,
  landcover_built_up_fraction: 0.02,
  landcover_bare_sparse_fraction: 0.5,
  landcover_snow_ice_fraction: 0,
  landcover_water_fraction: 0.01,
  landcover_herbaceous_wetland_fraction: 0.02,
  landcover_mangrove_fraction: 0,
  landcover_moss_lichen_fraction: 0,
};

function makeContextObservations(): ContextObservationOut[] {
  return contextFeaturesFixture.map((feature) => ({
    feature_id: feature.feature_id,
    label: feature.label,
    value: CONTEXT_VALUES[feature.feature_id] ?? 0,
    unit: "fraction",
    method_version: "worldcover-2021-v200-1.0.0",
    source_resolution_m: 10,
    valid_fraction: 0.98,
    quality: "verified",
    class_code: feature.class_code,
    provider: "ESA WorldCover",
    score_role: "context_only",
    dataset_id: "esa-worldcover-2021-v200",
  }));
}

export const datasetFixture: DatasetOut = {
  dataset_id: "copernicus-dem-glo90",
  provider: "European Space Agency",
  product_id: "COP-DEM-GLO-90",
  title: "Copernicus DEM GLO-90",
  url: "https://example.invalid/copernicus-dem-glo90",
  license: "Copernicus DEM licence",
  license_url: "https://example.invalid/licence",
  access_timestamp: "2026-11-01T00:00:00Z",
  source_crs: "EPSG:4326",
  native_resolution_m: 90,
  processing_version: "2023_1",
  checksum: null,
  source_kind: "earth_dem",
  notes: "Bilinear resampling to the 90 m analysis grid in a local metric CRS.",
};

export const worldCoverDatasetFixture: DatasetOut = {
  dataset_id: "esa-worldcover-2021-v200",
  provider: "European Space Agency (ESA) WorldCover / VITO",
  product_id: "ESA WorldCover 2021 v200",
  title: "ESA WorldCover 10 m 2021 v200 land cover",
  url: "https://esa-worldcover.org/en/data-access",
  license: "CC BY 4.0",
  license_url: "https://creativecommons.org/licenses/by/4.0/",
  access_timestamp: "2026-11-14T00:00:00Z",
  source_crs: "EPSG:4326",
  native_resolution_m: 10,
  processing_version: "worldcover-2021-v200-1.0.0",
  checksum: null,
  source_kind: "landcover",
  notes:
    "Land-cover context only, not mineralogy or geology. Attribution: © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium.",
};

const coverageFixture = {
  label: "indexed_subset",
  indexed_candidate_count: 4,
  geographic_bbox: { west: -116.9, south: -23.6, east: -13.5, north: 37.7 },
  description:
    "Search is limited to the Earth windows persisted in this index (curated seed windows plus any user-requested on-demand AOIs already analyzed). It is not a global survey, not an exhaustive top-10, and not a base-site recommendation.",
  data_kinds: ["observed", "synthetic"],
  origins: { indexed: 4, on_demand: 0 },
};

export const aoiAnalysisFixture = {
  max_side_m: 2000,
  min_side_m: 600,
  max_area_km2: 4,
  max_polygon_vertices: 100,
  analysis_resolution_m: 90,
  requested_features: [
    "slope_mean_deg",
    "slope_p90_deg",
    "terrain_relief_p95_p05_m",
    "roughness_std_m",
  ],
  supported_body: "Earth",
  note: "On-demand AOIs are bounded to a 2 km-class footprint so terrain support remains comparable to the indexed windows.",
  provider_status: {
    "copernicus-dem-glo90": { status: "verified", role: "terrain similarity" },
    "esa-worldcover-2021-v200": {
      status: "verified",
      role: "Earth land-cover context only (not mineralogy)",
    },
  },
};

export const capabilitiesFixture: CapabilitiesOut = {
  contract_version: "1.0.0",
  method_version: "terrain-metrics-1.0.0",
  data_snapshot_id: "dss-test-1",
  analysis: {
    window_size_m: 1980,
    analysis_resolution_m: 90,
    resampling: "bilinear",
    min_valid_fraction: 0.8,
  },
  features: featuresFixture,
  context_features: contextFeaturesFixture,
  supported_bodies: ["custom", "Moon"],
  indexed_candidate_count: 4,
  coverage: coverageFixture,
  available_reference_ids: ["moon-shackleton-rim-site04-roi2"],
  aoi_analysis: aoiAnalysisFixture,
  data_version: "dv-test-1",
};

export const noCoverageCapabilitiesFixture: CapabilitiesOut = {
  ...capabilitiesFixture,
  indexed_candidate_count: 0,
  coverage: {
    label: "indexed_subset",
    indexed_candidate_count: 0,
    geographic_bbox: null,
    description:
      "Search is limited to the Earth windows persisted in this index. It is not a global survey.",
    data_kinds: [],
    origins: { indexed: 0, on_demand: 0 },
  },
  available_reference_ids: [],
};

function resultOne(): SearchResultOut {
  return {
    rank: 1,
    location_id: "earth-observed-1",
    display_name: "Observed test window",
    lat: 34.2,
    lon: -116.1,
    bbox: { west: -116.11, south: 34.19, east: -116.09, north: 34.21 },
    distance: 0.037,
    dimension_distances: { terrain_shape: 0.037 },
    metrics: [
      {
        feature_id: "slope_mean_deg",
        unit: "degree",
        target: 12,
        candidate: 12.4,
        delta: 0.4,
        scale: 10,
        weight: 1,
        normalized_delta: 0.04,
        contribution: 0.0010667,
      },
      {
        feature_id: "terrain_relief_p95_p05_m",
        unit: "meter",
        target: 80,
        candidate: 86,
        delta: 6,
        scale: 200,
        weight: 0.5,
        normalized_delta: 0.03,
        contribution: 0.0003,
      },
    ],
    dataset_ids: ["copernicus-dem-glo90", "esa-worldcover-2021-v200"],
    data_kind: "observed",
    quality_flags: [],
    caveats: [
      "Terrain shape only; not a claim of physical or environmental equivalence.",
      "Earth land-cover context is informational only and is not lunar mineralogy, geology, or a similarity dimension.",
    ],
  };
}

function resultTwo(): SearchResultOut {
  return {
    rank: 2,
    location_id: "earth-synthetic-2",
    display_name: "Synthetic test window",
    lat: -22.8,
    lon: -15.1,
    bbox: { west: -15.11, south: -22.81, east: -15.09, north: -22.79 },
    distance: 0.6658,
    dimension_distances: { terrain_shape: 0.6658 },
    metrics: [
      {
        feature_id: "slope_mean_deg",
        unit: "degree",
        target: 12,
        candidate: 20,
        delta: 8,
        scale: 10,
        weight: 1,
        normalized_delta: 0.8,
        contribution: 0.4266667,
      },
      {
        feature_id: "terrain_relief_p95_p05_m",
        unit: "meter",
        target: 80,
        candidate: 120,
        delta: 40,
        scale: 200,
        weight: 0.5,
        normalized_delta: 0.2,
        contribution: 0.0133333,
      },
    ],
    dataset_ids: ["synthetic-demo-dem"],
    data_kind: "synthetic",
    quality_flags: ["synthetic_fixture"],
    caveats: [
      "DEMO / SYNTHETIC fixture values - not a real terrain observation.",
      "Terrain shape only; not a claim of physical or environmental equivalence.",
    ],
  };
}

export const searchResponseFixture: SearchResponse = {
  contract_version: "1.0.0",
  method_version: "terrain-metrics-1.0.0",
  query_version: "search-1.1.0",
  data_version: "dv-test-1",
  data_snapshot_id: "dss-test-1",
  coverage: coverageFixture,
  coverage_label: "indexed_subset",
  requested_features: ["slope_mean_deg", "terrain_relief_p95_p05_m"],
  filters_applied: {},
  indexed_candidate_count: 4,
  eligible_candidate_count: 3,
  excluded_candidate_count: 1,
  excluded: [
    {
      location_id: "earth-missing-metric",
      reason: "missing_metric:slope_p90_deg",
    },
  ],
  results: [resultOne(), resultTwo()],
  limitations: [
    "Only local terrain shape is compared. Gravity, atmosphere, radiation, regolith, mineralogy, temperature and geological history are not compared.",
    "Terrain-shape similarity is not physical equivalence and the distance is not a scientific similarity percentage.",
  ],
};

export const emptySearchResponseFixture: SearchResponse = {
  ...searchResponseFixture,
  eligible_candidate_count: 0,
  excluded_candidate_count: 3,
  excluded: [
    { location_id: "earth-missing-1", reason: "missing_metric:slope_mean_deg" },
    {
      location_id: "earth-missing-2",
      reason: "missing_metric:terrain_relief_p95_p05_m",
    },
    { location_id: "earth-incompatible", reason: "incompatible_method_version" },
  ],
  results: [],
};

function featureObservation(
  feature: FeatureOut,
  value: number,
): LocationOut["features"][number] {
  return {
    feature_id: feature.id,
    label: feature.label,
    value,
    unit: feature.unit,
    method_version: feature.method_version,
    extraction_scale_m: feature.extraction_scale_m,
    valid_fraction: 0.97,
    quality: "good",
    scale: feature.scale,
    description: feature.description,
  };
}

function latitudeFor(locationId: string): number {
  return locationId === "earth-synthetic-2" || locationId === "earth-aoi-test"
    ? -22.8
    : 34.2;
}

function longitudeFor(locationId: string): number {
  return locationId === "earth-synthetic-2" || locationId === "earth-aoi-test"
    ? -15.1
    : -116.1;
}

export function locationFixture(
  locationId = "earth-observed-1",
  dataKind: "observed" | "synthetic" = "observed",
  origin: "indexed" | "on_demand" = "indexed",
): LocationOut {
  return {
    location_id: locationId,
    display_name:
      origin === "on_demand"
        ? "On-demand Earth AOI test"
        : dataKind === "synthetic"
          ? "Synthetic test window"
          : "Observed test window",
    body: "Earth",
    lat: latitudeFor(locationId),
    lon: longitudeFor(locationId),
    bbox:
      dataKind === "synthetic"
        ? { west: -15.11, south: -22.81, east: -15.09, north: -22.79 }
        : { west: -116.11, south: 34.19, east: -116.09, north: 34.21 },
    window_size_m: origin === "on_demand" ? 1119.309 : 1980,
    analysis_resolution_m: origin === "on_demand" ? 91.481 : 90,
    crs: "EPSG:32611",
    valid_fraction: 0.97,
    data_kind: dataKind,
    quality_flags:
      origin === "on_demand"
        ? [
            "on_demand_aoi",
            "copernicus_dsm_may_include_vegetation_buildings",
            "worldcover_context_2021_epoch",
          ]
        : dataKind === "synthetic"
          ? ["synthetic_fixture"]
          : [],
    notes:
      dataKind === "synthetic"
        ? "DEMO / SYNTHETIC fixture."
        : origin === "on_demand"
          ? "User-requested on-demand AOI; source_nodata_undefined"
          : "Observed DEM window for test purposes.",
    area_km2: origin === "on_demand" ? 1.079562 : null,
    geometry: null,
    origin,
    features: featuresFixture.map((feature, index) =>
      featureObservation(feature, [12.4, 26.1, 86, 4.8][index] ?? 0),
    ),
    context_observations: makeContextObservations(),
    datasets:
      dataKind === "synthetic"
        ? [
            {
              ...datasetFixture,
              dataset_id: "synthetic-demo-dem",
              source_kind: "synthetic",
            },
            worldCoverDatasetFixture,
          ]
        : [datasetFixture, worldCoverDatasetFixture],
    known_limitations:
      dataKind === "synthetic"
        ? ["DEMO / SYNTHETIC fixture: values are not observations."]
        : [
            "Metrics describe terrain shape at a 2 km / 90 m analysis scale derived from the cited elevation source; they are not a full environmental characterization.",
            "ESA WorldCover class fractions are Earth land-cover context at a 2021 epoch. They are not mineral composition, not geology, and not comparable to lunar surface materials.",
          ],
    contract_version: "1.0.0",
    method_version: "terrain-metrics-1.0.0",
    data_version: "dv-test-1",
    data_snapshot_id: "dss-test-1",
  };
}

export const aoiLocationFixture: LocationOut = {
  ...locationFixture("earth-aoi-test", "observed", "on_demand"),
  features: featuresFixture.map((feature, index) =>
    featureObservation(feature, [18.994946, 29.209398, 230.479754, 41.865196][index] ?? 0),
  ),
};

export const contextFixture: ContextOut = {
  export_version: "1.0.0",
  note: "Grounded structured export for external AI assistance. Contains observed metadata and computed comparisons; no generated narrative.",
  generated_at: "2026-11-14T12:00:00Z",
  location: locationFixture(),
  query: { slope_mean_deg: 12, terrain_relief_p95_p05_m: 80 },
  source_facts: [
    "Dataset copernicus-dem-glo90: Copernicus DEM GLO-90 by European Space Agency.",
  ],
  computed_comparisons: [
    {
      feature_id: "slope_mean_deg",
      unit: "degree",
      target: 12,
      observed: 12.4,
      delta: 0.4,
      scale: 10,
      normalized_delta: 0.04,
    },
    {
      feature_id: "terrain_relief_p95_p05_m",
      unit: "meter",
      target: 80,
      observed: 86,
      delta: 6,
      scale: 200,
      normalized_delta: 0.03,
    },
  ],
  limitations: ["Terrain-shape similarity is not physical equivalence."],
};

export const referenceFixture: ReferenceOut = {
  reference_id: "moon-shackleton-rim-site04-roi2",
  body: "Moon",
  display_name: "Shackleton Rim (LOLA Site04 ROI 2)",
  dataset_id: "nasa-lola-site04-5mpp-surf",
  lat: -89.77192511,
  lon: -156.50226014,
  bbox: null,
  window_size_m: 1980,
  analysis_resolution_m: 90,
  crs: "Lunar south polar stereographic, MOON_ME, metres",
  valid_fraction: 1,
  quality_flags: [
    "5m_grid_interpolated_pixels",
    "official_pgda_lola_site04",
    "source_native_resolution_5m",
    "analysis_resolution_90m",
  ],
  notes:
    "Shackleton rim / lunar south pole window. Analysis window 1980 m resampled bilinearly to 90 m. The 5 m LDEM is about 90% interpolated; do not treat 5 m texture as an independent measurement.",
  features: featuresFixture.map((feature, index) =>
    ({
      feature_id: feature.id,
      label: feature.label,
      value: [17.895873, 28.777052, 390.805907, 102.805473][index] ?? 0,
      unit: feature.unit,
      method_version: feature.method_version,
      extraction_scale_m: 90,
      valid_fraction: 1,
      quality: "verified",
      scale: feature.scale,
      description: feature.description,
    }) satisfies LocationOut["features"][number],
  ),
};

export const analyzeQueuedFixture: AoiAnalyzeResponse = {
  job_id: "job-aoi-1",
  status: "queued",
  area_id: "earth-aoi-test",
  cache_key: "cache-key-1",
  cached: false,
  provider_status: {},
  result: null,
  links: {
    job: "/api/v1/jobs/job-aoi-1",
    area_features: "/api/v1/areas/earth-aoi-test/features",
  },
};

const baseJob: JobOut = {
  job_id: "job-aoi-1",
  status: "queued",
  area_id: "earth-aoi-test",
  provider_status: {},
  error: null,
  result: null,
  created_at: "2026-11-14T12:00:00Z",
  started_at: null,
  finished_at: null,
};

export const jobQueuedFixture: JobOut = { ...baseJob };

export const jobRunningFixture: JobOut = {
  ...baseJob,
  status: "running",
  provider_status: {},
  started_at: "2026-11-14T12:00:01Z",
};

const completedProviderStatus = {
  "copernicus-dem-glo90": {
    status: "verified",
    source_url: "https://example.invalid/copernicus.tif",
    tile: "Copernicus_DSM_COG_30_N29_00_W014_00_DEM",
  },
  "esa-worldcover-2021-v200": {
    status: "verified",
    source_url: "https://example.invalid/worldcover/",
    tiles: 1,
    valid_pixels: 14400,
  },
};

export const jobCompletedFixture: JobOut = {
  ...baseJob,
  status: "completed",
  provider_status: completedProviderStatus,
  result: {
    area_id: "earth-aoi-test",
    location_id: "earth-aoi-test",
    data_version: "dv-test-1",
    feature_count: 4,
    context_feature_count: 11,
    provider_status: completedProviderStatus,
    warnings: ["source_nodata_undefined"],
    area_km2: 1.079562,
  },
  started_at: "2026-11-14T12:00:01Z",
  finished_at: "2026-11-14T12:00:10Z",
};

export const jobPartialFixture: JobOut = {
  ...jobCompletedFixture,
  status: "partial",
  provider_status: {
    ...completedProviderStatus,
    "esa-worldcover-2021-v200": {
      status: "failed",
      reason: "context_provider_failed",
      message: "ESA WorldCover window read failed in test.",
    },
  },
  result: {
    ...(jobCompletedFixture.result ?? {}),
    provider_status: {
      ...completedProviderStatus,
      "esa-worldcover-2021-v200": {
        status: "failed",
        reason: "context_provider_failed",
      },
    },
    warnings: ["worldcover_context_provider_failed"],
  },
};

export const jobFailedFixture: JobOut = {
  ...baseJob,
  status: "failed",
  provider_status: { analysis: { status: "failed", reason: "source_fetch_failed" } },
  error: {
    code: "source_fetch_failed",
    message: "Copernicus DEM window read failed in test.",
  },
  started_at: "2026-11-14T12:00:01Z",
  finished_at: "2026-11-14T12:00:05Z",
};
