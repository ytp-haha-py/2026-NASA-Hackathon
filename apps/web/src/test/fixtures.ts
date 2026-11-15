import type {
  AoiAnalyzeResponse,
  CapabilitiesOut,
  ContextFeatureOut,
  ContextObservationOut,
  ContextOut,
  DatasetOut,
  DimensionsOut,
  FeatureOut,
  GlobalCandidateOut,
  GlobalCoverageOut,
  GlobalSearchResponse,
  GlobalSearchResultOut,
  JobOut,
  LocationOut,
  ReferenceOut,
  RefinePlanOut,
  SearchResponse,
  SearchResultOut,
  TypedAreaOut,
  TypedCoverageOut,
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
      "Candidates missing this metric are excluded only when it has a positive weight; zero-weight metrics are not compared.",
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
      "Candidates missing this metric are excluded only when it has a positive weight; zero-weight metrics are not compared.",
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
      "Candidates missing this metric are excluded only when it has a positive weight; zero-weight metrics are not compared.",
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
      "Candidates missing this metric are excluded only when it has a positive weight; zero-weight metrics are not compared.",
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

export const dimensionsFixture: DimensionsOut = {
  contract_version: "1.0.0",
  method_version: "terrain-metrics-1.0.0",
  dimensions: [
    {
      dimension_id: "terrain_shape",
      label: "Terrain shape",
      description: "Local terrain shape at 90 m analysis support.",
      score_role: "similarity",
      features: featuresFixture.map((feature) => ({
        id: feature.id,
        label: feature.label,
        unit: feature.unit,
        method_version: feature.method_version,
      })),
    },
  ],
  context_features: contextFeaturesFixture,
};

const typedSupport = {
  kind: "bbox",
  crs: "EPSG:4326",
  resolution_m: 250,
  window_size_m: null,
  bbox: { west: -116.11, south: 34.19, east: -116.09, north: 34.21 },
  bbox_crs: "EPSG:4326",
  area_km2: 3.96,
  source_resolution_m: 250,
  native_resolution_m: 250,
  valid_fraction: 0.97,
  notes: "Bounded fixture window for UI tests.",
};

export const typedAreaFixture: TypedAreaOut = {
  area_id: "earth-observed-1",
  body: "Earth",
  bbox: { west: -116.11, south: 34.19, east: -116.09, north: 34.21 },
  crs: "EPSG:4326",
  coordinate_frame: "earth_wgs84",
  area_km2: 3.96,
  observations: [
    {
      observation_id: "fixture:earth-observed-1:D02:geology",
      area_id: "earth-observed-1",
      body: "Earth",
      dimension_code: "D02",
      feature_id: "geology_analysis_availability",
      value_kind: "category",
      state: "unknown",
      value: null,
      unit: null,
      method_version: "geology-1.0.0",
      dataset_ids: ["glim-niveau-i-mirror"],
      source_asset_ids: ["geology-asset:glim:fixture"],
      quality_flags: ["redistribution_not_verified", "fixture_record"],
      score_role: "evidence_only",
      spatial_support: typedSupport,
      temporal_support: { kind: "unknown", start: null, end: null, notes: null },
      depth_support: { kind: "surface", min_m: null, max_m: null },
      uncertainty: null,
      notes: "Map overlay completed locally; no shareable value was exported.",
    },
    {
      observation_id: "fixture:earth-observed-1:D03:temperature",
      area_id: "earth-observed-1",
      body: "Earth",
      dimension_code: "D03",
      feature_id: "surface_air_temperature_2m_c",
      value_kind: "timeseries",
      state: "detected",
      value: {
        timestamps: ["2024-01-01", "2024-01-02"],
        values: [11.5, 12.7],
      },
      unit: "degree_C",
      method_version: "nasa-power-daily-1.0.0",
      dataset_ids: ["nasa-power-daily"],
      source_asset_ids: ["nasa-power:earth-observed-1:d03"],
      quality_flags: ["model_reanalysis_context"],
      score_role: "context_only",
      spatial_support: {
        ...typedSupport,
        native_resolution_m: 50000,
        source_resolution_m: 50000,
        resolution_m: 50000,
      },
      temporal_support: {
        kind: "interval",
        start: "2024-01-01T00:00:00Z",
        end: "2024-01-02T00:00:00Z",
        notes: "Daily model/reanalysis context.",
      },
      depth_support: { kind: "surface", min_m: null, max_m: null },
      uncertainty: {
        kind: "published_range",
        lower: 10.9,
        upper: 13.2,
        unit: "degree_C",
      },
      notes: "Coarse model context, not a local thermal-inertia measurement.",
    },
    {
      observation_id: "fixture:earth-observed-1:D05:detection",
      area_id: "earth-observed-1",
      body: "Earth",
      dimension_code: "D05",
      feature_id: "mineral_spectral_detection",
      value_kind: "detection",
      state: "not_detected",
      value: null,
      unit: "detection",
      method_version: "illumination-radiation-0.1.0",
      dataset_ids: ["nasa-power-daily"],
      source_asset_ids: ["nasa-power:earth-observed-1:d05"],
      quality_flags: ["fixture_record"],
      score_role: "evidence_only",
      spatial_support: typedSupport,
      temporal_support: { kind: "static" },
      depth_support: { kind: "surface" },
      uncertainty: null,
      notes: "The API state is not_detected; no numeric value is supplied.",
    },
    {
      observation_id: "fixture:earth-observed-1:D06:bulk-density",
      area_id: "earth-observed-1",
      body: "Earth",
      dimension_code: "D06",
      feature_id: "soil_bdod_0_5cm_mean",
      value_kind: "numeric",
      state: "detected",
      value: 131.5,
      unit: "cg cm-3",
      method_version: "soilgrids-wcs-2.0.0",
      dataset_ids: ["soilgrids-250m-v2"],
      source_asset_ids: ["soilgrids-wcs:earth-observed-1:bdod"],
      quality_flags: ["model_derived_soil_property", "250m_native_grid", "fixture_record"],
      score_role: "evidence_only",
      spatial_support: typedSupport,
      temporal_support: { kind: "unknown" },
      depth_support: { kind: "depth_interval", min_m: 0, max_m: 0.05 },
      uncertainty: null,
      notes: "Model-derived soil property; not a local sample.",
    },
  ],
};

export const emptyTypedAreaFixture: TypedAreaOut = {
  ...typedAreaFixture,
  observations: [],
};

export const typedCoverageFixture: TypedCoverageOut = {
  total: 4,
  by_dimension: {
    D02: { total: 1, by_body: { Earth: 1 }, by_state: { unknown: 1 } },
    D03: { total: 1, by_body: { Earth: 1 }, by_state: { detected: 1 } },
    D05: { total: 1, by_body: { Earth: 1 }, by_state: { not_detected: 1 } },
    D06: { total: 1, by_body: { Earth: 1 }, by_state: { detected: 1 } },
  },
  scope_note:
    "Counts cover typed-v2 records in this fixture database only. Missing sources are not zero-filled.",
};

export const coarseFeatureFixtures = [
  {
    feature_id: "coarse_slope_mean_deg",
    label: "Coarse mean slope (250 m source)",
    unit: "degree",
    method_version: "coarse-terrain-250m-1.0.0",
    min_value: 0,
    max_value: 90,
    scale: 10,
    spatial_support: "5 km candidate window aggregated from ~250 m slope pixels",
    description: "Mean of valid Geomorpho250 slope pixels within the 5 km window.",
    score_role: "similarity",
    dimension_code: "D01",
    source_resolution_m: 250,
    grid_window_m: 5000,
  },
  {
    feature_id: "coarse_slope_p90_deg",
    label: "Coarse 90th percentile slope",
    unit: "degree",
    method_version: "coarse-terrain-250m-1.0.0",
    min_value: 0,
    max_value: 90,
    scale: 15,
    spatial_support: "5 km candidate window aggregated from ~250 m slope pixels",
    description: "Linear 90th percentile of valid Geomorpho250 slope pixels.",
    score_role: "similarity",
    dimension_code: "D01",
    source_resolution_m: 250,
    grid_window_m: 5000,
  },
  {
    feature_id: "coarse_flat_fraction_lt5deg",
    label: "Coarse flat fraction (<5°)",
    unit: "fraction",
    method_version: "coarse-terrain-250m-1.0.0",
    min_value: 0,
    max_value: 1,
    scale: 0.25,
    spatial_support: "5 km candidate window aggregated from ~250 m slope pixels",
    description: "Fraction of valid slope pixels strictly below 5 degrees.",
    score_role: "similarity",
    dimension_code: "D01",
    source_resolution_m: 250,
    grid_window_m: 5000,
  },
];

export const globalCoverageFixture: GlobalCoverageOut = {
  status: "ready",
  snapshot_id: "fixture-coarse-2026-11",
  label: "local_coarse_index_not_global_exhaustive",
  row_count: 12,
  regions: ["fixture-region-a", "fixture-region-b"],
  bbox: { west: -118.5, south: 33.2, east: -114.8, north: 36.8 },
  method_version: "coarse-terrain-250m-1.0.0",
  features: coarseFeatureFixtures,
  note: "DEMO / FIXTURE coarse index for UI tests. Not a global survey.",
};

export const globalNoIndexCoverageFixture: GlobalCoverageOut = {
  status: "no_index",
  snapshot_id: null,
  label: "no_coarse_index",
  row_count: 0,
  regions: [],
  bbox: null,
  method_version: null,
  features: coarseFeatureFixtures,
  note: "No coarse Parquet index is present. Global discovery is intentionally not claimed.",
};

function globalResult(
  rank: number,
  candidateId: string,
  regionId: string,
  west: number,
): GlobalSearchResultOut {
  return {
    rank,
    candidate_id: candidateId,
    grid_id: `grid-${candidateId}`,
    region_id: regionId,
    body: "Earth",
    bbox: { west, south: 34.2, east: west + 0.04, north: 34.24 },
    crs: "EPSG:4326",
    area_km2: 25,
    window_size_m: 5000,
    stride_m: 5000,
    source_resolution_m: 250,
    slope_mean_deg: 9.5 + rank,
    slope_p90_deg: 16.2 + rank,
    flat_fraction: 0.42 - rank / 100,
    valid_fraction: 0.96,
    method_version: "coarse-terrain-250m-1.0.0",
    distance: 0.12 + rank / 100,
    qa_flags: ["fixture_coarse_row"],
  };
}

export const globalSearchResponseFixture: GlobalSearchResponse = {
  snapshot_id: "fixture-coarse-2026-11",
  method_version: "coarse-terrain-250m-1.0.0",
  index_version: "coarse-index-1.0.0",
  coverage_label: "local_coarse_index_not_global_exhaustive",
  indexed_candidate_count: 12,
  eligible_candidate_count: 9,
  excluded_candidate_count: 3,
  results: [
    globalResult(1, "fixture-candidate-001", "fixture-region-a", -116.4),
    globalResult(2, "fixture-candidate-002", "fixture-region-b", -115.2),
  ],
  note: "Coarse candidates are a shortlist, not the fine 90 m terrain result.",
};

export const globalEmptySearchResponseFixture: GlobalSearchResponse = {
  ...globalSearchResponseFixture,
  eligible_candidate_count: 0,
  excluded_candidate_count: 12,
  results: [],
};

export function globalCandidateFixture(
  candidateId = "fixture-candidate-001",
): GlobalCandidateOut {
  return {
    candidate_id: candidateId,
    grid_id: `grid-${candidateId}`,
    region_id: "fixture-region-a",
    body: "Earth",
    bbox: { west: -116.4, south: 34.2, east: -116.36, north: 34.24 },
    crs: "EPSG:4326",
    area_km2: 25,
    window_size_m: 5000,
    stride_m: 5000,
    source_resolution_m: 250,
    slope_mean_deg: 10.5,
    slope_p90_deg: 17.2,
    flat_fraction: 0.41,
    valid_fraction: 0.96,
    method_version: "coarse-terrain-250m-1.0.0",
    grid_version: "coarse-grid-1.0.0",
    dataset_id: "geomorpho250-fixture",
    source_asset_id: "geomorpho250:fixture:001",
    qa_flags: ["fixture_coarse_row"],
  };
}

export const refinePlanFixture: RefinePlanOut = {
  candidate_id: "fixture-candidate-001",
  parent_method_version: "coarse-terrain-250m-1.0.0",
  fine_method_version: "terrain-metrics-1.0.0",
  windows: [
    {
      fine_window_id: "fixture-candidate-001:fine:0",
      parent_candidate_id: "fixture-candidate-001",
      bbox_wgs84: { west: -116.4, south: 34.2, east: -116.39, north: 34.21 },
      window_size_m: 1980,
      method_version: "terrain-metrics-1.0.0",
    },
    {
      fine_window_id: "fixture-candidate-001:fine:1",
      parent_candidate_id: "fixture-candidate-001",
      bbox_wgs84: { west: -116.39, south: 34.2, east: -116.38, north: 34.21 },
      window_size_m: 1980,
      method_version: "terrain-metrics-1.0.0",
    },
  ],
  note: "Plan only. The existing 90 m pipeline must be invoked separately.",
};
