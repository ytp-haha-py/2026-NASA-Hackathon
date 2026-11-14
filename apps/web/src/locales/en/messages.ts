/**
 * English UI messages: the canonical key set and the fallback for every other locale.
 *
 * - Placeholders use `{name}`. A placeholder without a matching variable is left as-is,
 *   so literal API paths such as `GET /locations/{id}` are safe.
 * - Plural messages use `_one` / `_other` suffixes and are rendered with `tp()`.
 * - Messages describe UI only. Never put scientific values, dataset names or API
 *   identifiers here; those are rendered from the API response unchanged.
 */
export const en = {
  // Language switcher
  "language.label": "Language",

  // Common
  "common.errorCode": "Error code: {code}",
  "common.httpStatus": " · HTTP {status}",
  "common.validationDetails": "Validation details",
  "common.notReported": "not reported",
  "common.noneReported": "none reported",
  "common.none": "none",
  "common.pending": "pending",
  "common.unknown": "unknown",
  "common.sourceTextNote": "",
  "common.codeMeaning": " ({meaning})",

  // Formatting templates
  "format.bbox": "lat {south}° to {north}°, lon {west}° to {east}°",
  "format.windowAt": "{window} m at {resolution} m",
  "format.km2": "{value} km²",
  "format.meters": "{value} m",

  // App shell
  "app.skipLink": "Skip to content",
  "app.eyebrow": "Terrain-shape similarity · indexed + on-demand Earth",
  "app.title": "Planetary Analog Explorer",
  "app.summary":
    "Compare a hypothetical terrain-shape target with a committed Earth index, or analyze a bounded on-demand AOI. Land-cover context is shown separately and is never part of the similarity score. This is not a global survey, not a base-site recommendation, and not a physical-equivalence claim.",
  "app.capabilitiesLoading": "Loading capabilities and indexed coverage…",
  "app.capabilitiesErrorTitle": "Capabilities could not be loaded.",
  "app.capabilitiesErrorHelp":
    "The search form needs GET /api/v1/capabilities. Check that the API is running and reachable through the Vite proxy.",
  "app.retryLoading": "Retry loading",
  "app.tabsLabel": "Analysis mode",
  "app.tabSearch": "Search target",
  "app.tabAoi": "Analyze AOI",
  "app.coverageEyebrow": "Searchable coverage",
  "app.coverageHeading_one": "{count} searchable Earth region",
  "app.coverageHeading_other": "{count} searchable Earth regions",
  "app.coverageOrigins":
    "Origins: {indexed} + {onDemand}. On-demand AOIs are user-created and are never global coverage.",
  "app.coverageExtent": "Approximate coverage extent: {bbox}",
  "app.coverageExtentMissing": "No geographic coverage extent is available.",
  "app.noCandidates":
    "No searchable Earth candidates are available. A search may return HTTP 409 {code}; this UI does not invent fallback data.",
  "app.searching": "Searching searchable Earth regions…",
  "app.noIndexedTitle": "No indexed Earth data (HTTP 409)",
  "app.searchFailed": "Search failed",
  "app.searchErrorHelp":
    "Nothing is substituted when the API errors. Adjust the target or retry when the API is available.",
  "app.inspectTitle": "Inspect a candidate",
  "app.inspectBody":
    "Select a result by its button in the ranked list (or a pin/rectangle on the map) to load GET /locations/{id} and its grounded context export.",
  "app.idleHeading": "How this MVP answers",
  "app.idleBody":
    "The prefilled target is a hypothetical example, not a NASA measurement. A search returns the closest windows within the searchable coverage using a weighted normalized Euclidean distance on terrain shape.",
  "app.idleItemDistance": "Lower distance means more similar. It is never a percentage.",
  "app.idleItemSynthetic":
    "Observed and DEMO / SYNTHETIC records are labeled explicitly; synthetic fixtures are not evidence.",
  "app.idleItemOnDemand":
    "On-demand AOIs are user-created windows, not global coverage. Their origin appears in result caveats.",
  "app.idleItemNotCompared":
    "Gravity, atmosphere, radiation, regolith, mineralogy, temperature, and geological history are not compared. ESA WorldCover context is not mineralogy.",
  "app.noCapabilities":
    "No capabilities were returned. Search and AOI analysis are unavailable.",
  "app.footerVersions":
    "Planetary Analog Explorer MVP · contract {contract} · method {method} · data {data} · snapshot {snapshot}",
  "app.footerNote":
    "Distance is a weighted normalized Euclidean value on terrain shape only. Basemap © OpenStreetMap contributors.",

  // Coverage counts (shared)
  "coverage.indexedCount": "{count} indexed",
  "coverage.onDemandCount": "{count} on-demand AOI",

  // Search form
  "search.step": "Step 1",
  "search.heading": "Describe a terrain-shape target",
  "search.intro":
    "Every metric is computed from 90 m elevation cells inside a 1,980 m analysis window. Only these local terrain-shape dimensions are compared.",
  "search.errorsTitle": "Check the target before searching.",
  "search.presetLegend": "Preset body",
  "search.bodyPreset": "Body preset",
  "search.bodyCustom": "Custom target — search indexed Earth",
  "search.bodyReference": "{body} reference",
  "search.bodyUnavailableSuffix": " — unavailable",
  "search.referenceRecord": "Reference record",
  "search.noReference": "No reference — use the custom target",
  "search.referenceHint":
    "Selecting a reference switches to that body and uses the reference's returned observation values as the search target. Manual target inputs are not sent for reference searches.",
  "search.presetsLoadFailed_one":
    "Reference records could not be loaded ({error}), so the {bodies} preset remains unavailable. They are not simulated.",
  "search.presetsLoadFailed_other":
    "Reference records could not be loaded ({error}), so the {bodies} presets remain unavailable. They are not simulated.",
  "search.presetsUnavailable_one":
    "The {bodies} preset is unavailable because GET /references returned no reference records for that body. They are not simulated.",
  "search.presetsUnavailable_other":
    "The {bodies} presets are unavailable because GET /references returned no reference records for those bodies. They are not simulated.",
  "search.referenceTargetHint":
    "Reference target supplied by GET /references for {name}. Weights default to 1.0 for every compatible reference feature.",
  "search.referenceDetails": "Selected reference details",
  "search.factBody": "Body",
  "search.factDataset": "Dataset",
  "search.factWindow": "Window / resolution",
  "search.factValidFraction": "Valid fraction",
  "search.referenceTableCaption": "Reference target observations supplied by GET /references",
  "search.colFeature": "Feature",
  "search.colValue": "Value",
  "search.colQuality": "Quality",
  "search.colValidFraction": "Valid fraction",
  "search.qualityFlags": "Reference quality flags",
  "search.lolaCaveatTitle": "LOLA reference caveat:",
  "search.lolaCaveat":
    "the source is the NASA LRO-LOLA 5 m/pixel Site04 DEM; about 90% of its 5 m pixels are interpolated, and these observations were resampled to the 90 m analysis grid. Do not treat 5 m texture as independent measurements. Moon coordinates use the source planetary frame, not Earth WGS84.",
  "search.referenceNotes": "Reference notes from the API",
  "search.referenceMetricsLegend": "Reference target metrics (read-only)",
  "search.customMetricsLegend": "Custom target metrics",
  "search.referenceMetricsHint":
    "Values are read from the selected GET /references record. They are not manually entered target values and are not a claim of physical equivalence.",
  "search.exampleTitle": "Hypothetical example, not a NASA measurement.",
  "search.exampleBody":
    "The prefilled values are illustrative starting points; edit or replace them. Enabling a metric adds it to the target. Metrics with weight 0 are omitted from the request so they do not filter candidates.",
  "search.unitTag": "unit: {unit}",
  "search.validDomain": "Valid domain: {domain}",
  "search.include": "Include {label}",
  "search.targetValue": "Target value ({unit})",
  "search.weight": "Weight (≥ 0)",
  "search.fieldValue": "value",
  "search.fieldWeight": "weight",
  "search.fieldError": "{label} {field}: {message}",
  "search.errFiniteNumber": "Enter a finite number.",
  "search.errRange": "Enter a value between {min} and {max} {unit}.",
  "search.errWeight": "Weight must be a finite number greater than or equal to 0.",
  "search.errLimit": "Maximum results must be a whole number between 1 and 10.",
  "search.errFilterRequired": "{label} requires a value in the closed interval [0, 1].",
  "search.errFilterRange": "{label} must be a finite fraction from 0 to 1.",
  "search.filterBareName": "Bare/sparse minimum",
  "search.filterBuiltName": "Built-up maximum",
  "search.errSelectReference": "Select a {body} reference record before searching.",
  "search.errReferenceEmpty":
    "The selected {body} reference contains no compatible feature observations.",
  "search.errNoMetric":
    "Choose at least one metric with a positive weight. Metrics with weight 0 are omitted.",
  "search.errNoReferenceValues":
    "This reference preset has no usable target values. Choose another reference or a custom target.",
  "search.errAoiNoValues":
    "The completed AOI did not return any similarity feature values that can be used as a target.",
  "search.filtersLegend": "Optional Earth land-cover filters (context only)",
  "search.filtersHint":
    "These are hard subset filters on ESA WorldCover context fractions. They do not enter the distance and are not mineralogy or geology. Filtered-out candidates are counted as exclusions by the API.",
  "search.bareFilterLabel": "Only include candidates with {label} ≥",
  "search.bareFallback": "bare/sparse fraction",
  "search.builtFilterLabel": "Only include candidates with {label} ≤",
  "search.builtFallback": "built-up fraction",
  "search.minFraction": "Minimum fraction (0–1)",
  "search.maxFraction": "Maximum fraction (0–1)",
  "search.limitLabel": "Maximum results (1–10)",
  "search.limitHint":
    "The API returns min(limit, eligible indexed windows). Lower distance means more similar; it is not a percentage.",
  "search.submit": "Search indexed Earth candidates",
  "search.submitting": "Searching indexed Earth candidates…",
  "search.submitHint":
    "This searches only the committed indexed subset. It is not a global survey and not a base-site recommendation.",

  // Results
  "results.step": "Step 2",
  "results.heading": "Ranked matches",
  "results.headline": "Top {shown} among {total} {scope} regions",
  "results.scopeIndexed": "indexed",
  "results.scopeMixed": "indexed and on-demand",
  "results.originSplit":
    "Origin split at this response: {indexed} + {onDemand}. On-demand AOIs are user-created and are not global coverage.",
  "results.eligibility":
    "Eligible after metric availability checks: {eligible}. Excluded: {excluded}. Coverage label: {label}.",
  "results.requestedFeatures":
    "Requested similarity features: {features}. Context filters: {filters}.",
  "results.filterMin": "min {value}",
  "results.filterMax": "max {value}",
  "results.filterApplied": "applied",
  "results.notGlobalTitle": "This is not a global top 10.",
  "results.notGlobalBody":
    "It is the closest set within the searchable coverage only, not a complete ranking, not a survey, and not a base-site recommendation.",
  "results.versions":
    "Query {query} · method {method} · data {data} · snapshot {snapshot}",
  "results.scrollHint":
    "On narrow screens, metric tables scroll horizontally to show every column.",
  "results.syntheticCount_one": "{count} of {total} returned record is DEMO / SYNTHETIC.",
  "results.syntheticCount_other": "{count} of {total} returned records are DEMO / SYNTHETIC.",
  "results.syntheticBody":
    "Synthetic fixture values are not real terrain observations and must not be cited as evidence.",
  "results.emptyTitle": "No eligible indexed candidates",
  "results.emptyBody":
    "The search reached the index, but no candidate met the target's metric availability or context-filter requirements. This does not mean no real place on Earth is similar; the searchable coverage is not a global survey.",
  "results.emptyExcluded_one": "{count} candidate was excluded. See the reasons below.",
  "results.emptyExcluded_other": "{count} candidates were excluded. See the reasons below.",
  "results.noneExcluded": "No indexed candidates were excluded for missing metrics.",
  "results.excludedSummary_one": "{count} excluded candidate — show reasons",
  "results.excludedSummary_other": "{count} excluded candidates — show reasons",
  "results.limitations": "Search limitations returned by the API",
  "results.selectLabel": "Select rank {rank}: {name}",
  "results.distance": "distance",
  "results.onDemandBadge": "On-demand AOI",
  "results.tableCaption":
    "Target comparison for {name}. Lower distance is more similar. Distance is not a percentage.",
  "results.colMetric": "Metric",
  "results.colCandidate": "Candidate",
  "results.colTarget": "Target",
  "results.colDelta": "Delta",
  "results.weightScale": "weight {weight} · scale {scale}",
  "results.caveats": "Caveats for this record",
  "results.dimensionDistances": "Dimension distances: {values}",
  "results.datasetIds": "Dataset IDs: {ids}",

  // Exclusion reasons
  "exclusion.missingMetric": "Missing required metric: {names}",
  "exclusion.missingContext": "Missing land-cover context: {names}",
  "exclusion.filter": "Excluded by land-cover filter: {names}",
  "exclusion.incompatibleMethod": "Incompatible method version",

  // Data kind badge
  "dataKind.syntheticLabel": "DEMO / SYNTHETIC data: not a real terrain observation",
  "dataKind.synthetic": "DEMO / SYNTHETIC",
  "dataKind.observedLabel": "Observed data",
  "dataKind.observed": "Observed",

  // Map
  "map.eyebrow": "Spatial view",
  "map.heading": "Indexed result windows",
  "map.intro":
    "Only returned results are plotted. Pins and rectangles are not an exhaustive index, and the basemap is for orientation only — not scientific evidence.",
  "map.regionLabel": "Map of returned indexed Earth candidate windows",
  "map.basemapUnavailable":
    "Basemap tiles are unavailable (offline or blocked). The ranked list and record details remain fully usable without the basemap.",
  "map.noWindows": "No result windows to plot yet.",
  "map.plotted_one":
    "{count} returned window plotted. Click a pin or window rectangle to select it. Zoom in to see the 1,980 m window rectangles at this scale.",
  "map.plotted_other":
    "{count} returned windows plotted. Click a pin or window rectangle to select it. Zoom in to see the 1,980 m window rectangles at this scale.",
  "map.tooltip": "#{rank} {name} · distance {distance}",
  "map.attribution":
    "Basemap © OpenStreetMap contributors, available under the Open Database License.",

  // Detail panel
  "detail.step": "Step 3",
  "detail.heading": "Selected Earth candidate",
  "detail.rankDistance":
    "Rank #{rank} · distance {distance} (lower is more similar; not a percentage)",
  "detail.loading": "Loading location record…",
  "detail.failed": "Location record failed.",
  "detail.failedWithCode": "Location record failed ({code}).",
  "detail.retry": "Retry location record",

  // Location details
  "location.coordinates": "Coordinates",
  "location.dataKind": "Data kind",
  "location.origin": "Origin",
  "location.originOnDemand": "On-demand AOI (user-requested; not fixed coverage)",
  "location.originIndexed": "Indexed Earth window",
  "location.aoiArea": "AOI area",
  "location.analysisWindow": "Analysis window",
  "location.windowValue": "{window} m at {resolution} m resolution",
  "location.windowBbox": "Window bbox",
  "location.crs": "CRS",
  "location.validFraction": "Valid fraction",
  "location.qualityFlags": "Quality flags",
  "location.versions": "Versions",
  "location.versionsValue": "contract {contract} · method {method} · data {data}",
  "location.snapshot": "snapshot {snapshot}",
  "location.geometry": "Geometry",
  "location.geometryBboxOnly": "BBox-only request (no polygon geometry)",
  "location.geometryRing": "{type} ({count} outer-ring positions)",
  "location.recordNote": "API record note:",
  "location.onDemandNoteTitle": "On-demand AOI:",
  "location.onDemandNote":
    "this window was requested by a user and is searchable, but it is not part of a fixed global index or a complete survey.",
  "location.featuresHeading": "Observed terrain-shape features",
  "location.featuresIntro":
    "Values as returned by {source}. Units and valid fractions are record facts, not UI conversions.",
  "location.scrollHint": "On narrow screens, record tables scroll horizontally.",
  "location.noFeatures": "No feature observations were returned for this location.",
  "location.featuresCaption": "Terrain-shape feature observations for {name}",
  "location.colFeature": "Feature",
  "location.colValue": "Value",
  "location.colQuality": "Quality",
  "location.colValidFraction": "Valid fraction",
  "location.colExtractionScale": "Extraction scale",
  "location.comparisonHeading": "Requested target comparison",
  "location.comparisonIntro":
    "These comparisons come from the search response, not from a new calculation. “Close” is a display aid for |delta| ≤ 0.5 × the fixed metric scale; it is not a scientific significance threshold.",
  "location.noComparison":
    "This record was not returned by a search request, so no target comparison is available. Use the AOI as a search target to compare it with the index.",
  "location.comparisonCaption": "Requested target versus candidate for {name}",
  "location.colMetric": "Metric",
  "location.colCandidate": "Candidate",
  "location.colTarget": "Target",
  "location.colDelta": "Delta",
  "location.colDisplay": "Display",
  "location.normalizedDelta": "normalized delta {delta} · scale {scale}",
  "location.close": "Close",
  "location.differs": "Differs",
  "location.contextHeading":
    "Earth land-cover context (ESA WorldCover 2021) — context only, NOT mineralogy/geology, excluded from the similarity score",
  "location.contextWarning":
    "These class fractions describe Earth land cover for this footprint. They are context only: they are not mineralogy, not geology, and are never entered into the weighted normalized Euclidean distance.",
  "location.noContext": "No land-cover context observations were returned for this location.",
  "location.contextCaption": "ESA WorldCover land-cover context observations for {name}",
  "location.colClass": "Land-cover class",
  "location.colClassCode": "Class code",
  "location.colFraction": "Fraction",
  "location.colProvider": "Provider",
  "location.colSourceResolution": "Source resolution",
  "location.contextSeparate":
    "Context observations are shown separately and are not counted as similarity metrics or displayed in the comparison table above.",
  "location.provenanceHeading": "Source, license and access",
  "location.noDatasets": "No dataset provenance records were returned for this location.",
  "location.recordLimitsHeading": "Record limitations returned by the API",
  "location.noRecordLimits": "No record-specific limitations were returned.",
  "location.generalLimitsHeading": "General limitations (not record-specific)",
  "location.limitEquivalence":
    "Terrain-shape similarity is not physical or environmental equivalence.",
  "location.limitDistance":
    "Distance is a weighted normalized Euclidean value where lower is more similar. It is not a similarity percentage and has no physical unit.",
  "location.limitWindow":
    "Metrics describe only a 1,980 m window at 90 m analysis resolution; smaller landforms and processes are not resolved or compared.",
  "location.limitDem":
    "Source DEMs are surface models. Vegetation or buildings can bias slope and roughness in non-bare terrain.",
  "location.limitCoverage":
    "Only the indexed subset plus user-requested on-demand AOIs are searched. Absence from results is not evidence that no similar terrain exists. On-demand AOIs are never global coverage.",
  "location.limitWorldCover":
    "ESA WorldCover fractions are Earth land-cover context from a 2021 epoch. They are not mineralogy or geology and are not comparable to lunar surface materials.",
  "location.limitSynthetic":
    "DEMO / SYNTHETIC records are fixtures, not observations, and must not be cited as evidence.",
  "location.notSimulatedHeading": "What is NOT simulated or compared",
  "location.notSimulatedIntro":
    "This tool does not simulate or compare the following. It makes no habitability, engineering, operational, or mission-suitability claim. Land-cover fractions do not substitute for any of these dimensions.",
  "location.notGravity": "Gravity",
  "location.notAtmosphere": "Atmosphere",
  "location.notRadiation": "Radiation",
  "location.notRegolith": "Regolith",
  "location.notMineralogy": "Mineralogy",
  "location.notTemperature": "Temperature",
  "location.notGeologicalHistory": "Geological history",

  // Dataset facts (shared by detail and sources)
  "dataset.provider": "Provider",
  "dataset.productId": "Product ID",
  "dataset.product": "Product",
  "dataset.datasetId": "Dataset ID",
  "dataset.accessTimestamp": "Access timestamp",
  "dataset.license": "License",
  "dataset.processingVersion": "Processing version",
  "dataset.sourceKind": "Source kind",
  "dataset.sourceCrs": "Source CRS",
  "dataset.nativeResolution": "Native resolution",
  "dataset.checksum": "Checksum",
  "dataset.sourceLink": "Source product link",
  "dataset.recordLink": "Dataset record",

  // Context export
  "export.step": "Step 4",
  "export.heading": "Grounded context export",
  "export.intro":
    "A structured JSON export for later external AI use. It contains source facts and computed comparisons only. {notChatbot} and does not generate a narrative or recommendation. When present, Earth land-cover context observations are included and marked {code}; they are not similarity inputs.",
  "export.notChatbot": "This app is not a chatbot",
  "export.needsTarget": "A search target is required before a context export can be prepared.",
  "export.loading": "Preparing context export…",
  "export.failed": "Context export failed.",
  "export.failedWithCode": "Context export failed ({code}).",
  "export.retry": "Retry context export",
  "export.version": "Export version",
  "export.generatedAt": "Generated at",
  "export.download": "Download JSON",
  "export.copy": "Copy JSON",
  "export.preview": "Preview export JSON",
  "export.downloadUnsupported": "Download is not supported by this browser. Use Copy JSON instead.",
  "export.downloadStarted": "Download started.",
  "export.copied": "Copied the context JSON to the clipboard.",
  "export.copyFailed": "Copy failed: {reason}",
  "export.copyRejected": "Clipboard copy was rejected by the browser.",
  "export.copyUnknown": "unknown clipboard error",

  // Sources and methods
  "sources.eyebrow": "Provenance",
  "sources.heading": "Sources, coverage and methods",
  "sources.disclaimerHeading": "Indexing and coverage disclaimer",
  "sources.candidateRegions": "Searchable candidate regions: {count} (coverage label {label}).",
  "sources.originSplit":
    "Origin split: {indexed} + {onDemand}. On-demand AOIs are user-created windows and are never global coverage.",
  "sources.extent": "Geographic extent: {extent}",
  "sources.extentMissing": "not available — there may be no searchable windows.",
  "sources.dataKinds": "Data kinds present: {kinds}.",
  "sources.closestOnly":
    "Results are the closest matches among the searchable coverage only. This is not a global survey, not an exhaustive top-10, and not a base-site recommendation. Absence from results does not mean a location is a poor analog.",
  "sources.methodHeading": "Analysis method",
  "sources.contractVersion": "Contract version",
  "sources.methodVersion": "Method version",
  "sources.analysisWindow": "Analysis window",
  "sources.analysisResolution": "Analysis resolution",
  "sources.dataVersion": "Data version",
  "sources.dataSnapshot": "Data snapshot",
  "sources.aoiLimits": "On-demand AOI limits",
  "sources.aoiLimitsValue":
    "{min}–{max} m per side · ≤ {area} km² · latitude −80° to 84°",
  "sources.distance": "Distance",
  "sources.distanceValue":
    "Weighted normalized Euclidean; lower is more similar. Not a percentage.",
  "sources.aoiProviders": "AOI provider readiness",
  "sources.validDomain": "Valid domain",
  "sources.fixedScale": "Fixed scale",
  "sources.scaleRationale": "Scale rationale",
  "sources.extractionScale": "Extraction scale",
  "sources.method": "Method",
  "sources.missingPolicy": "Missing policy",
  "sources.contextHeading": "Context-only land cover (excluded from similarity)",
  "sources.contextBody":
    "ESA WorldCover 2021 v200 class fractions are Earth land-cover context. They are not mineralogy or geology, are not comparable to lunar surface materials, and are never included in the weighted normalized Euclidean distance.",
  "sources.contextShow_one": "Show {count} context-only feature definition",
  "sources.contextShow_other": "Show {count} context-only feature definitions",
  "sources.classTag": "class {code}",
  "sources.contextRole": "Role: {role} · unit: {unit} · method: {method}",
  "sources.worldCoverReference": "ESA WorldCover reference",
  "sources.worldCoverAttribution":
    "Data attribution: © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium.",
  "sources.datasetsHeading": "Dataset provenance",
  "sources.datasetsIntro":
    "Records below come directly from GET /datasets. They identify the source, license, access time, and processing version; they are not decorative imagery.",
  "sources.datasetsFailed": "Dataset provenance could not be loaded.",
  "sources.noDatasets": "No dataset provenance records were returned by the API.",

  // AOI form
  "aoiForm.eyebrow": "On-demand analysis",
  "aoiForm.heading": "Analyze a new Earth AOI",
  "aoiForm.intro":
    "Request a bounded Copernicus DEM terrain window plus ESA WorldCover land-cover context. Land cover is context only and never enters the similarity distance.",
  "aoiForm.errorsTitle": "Check the AOI before submitting.",
  "aoiForm.bboxLegend": "Bounding box (WGS84 decimal degrees)",
  "aoiForm.exampleTitle": "Example bbox for interface testing, not a site recommendation.",
  "aoiForm.exampleBody":
    "Replace west, south, east, and north with your own area. This MVP supports bbox requests; GeoJSON Polygon is accepted by the API but not drawn by this UI.",
  "aoiForm.west": "West longitude (°)",
  "aoiForm.south": "South latitude (°)",
  "aoiForm.east": "East longitude (°)",
  "aoiForm.north": "North latitude (°)",
  "aoiForm.measurement":
    "Approx. {width} m × {height} m · {area} km² (server recomputes on WGS84).",
  "aoiForm.measurementMissing": "Enter finite coordinates to preview the approximate AOI size.",
  "aoiForm.limitSide": "Side length: {min}–{max} m",
  "aoiForm.limitArea": "Area: at most {area} km²",
  "aoiForm.limitLatitude": "Latitude: {min}° to {max}° (provider coverage)",
  "aoiForm.limitResolution": "Analysis resolution: {resolution} m",
  "aoiForm.limitBody": "Body: {body}",
  "aoiForm.providerReadiness": "Provider readiness",
  "aoiForm.noProviders": "No provider status was advertised by capabilities.",
  "aoiForm.submit": "Analyze this Earth AOI",
  "aoiForm.submitting": "Analyzing Earth AOI…",
  "aoiForm.submitHint":
    "Cached AOIs return immediately; otherwise the API runs a durable job. Results are user-requested on-demand windows, not part of a global survey.",
  "aoiForm.errFinite": "All four bbox coordinates must be finite decimal degrees.",
  "aoiForm.errLongitude":
    "Longitude must satisfy -180 ≤ west < east ≤ 180. Antimeridian-crossing AOIs are not supported.",
  "aoiForm.errLatitudeOrder": "Latitude must satisfy south < north.",
  "aoiForm.errLatitudeRange":
    "Latitude must stay within {min}° to {max}° for the Earth provider coverage.",
  "aoiForm.errTooSmall": "AOI too small: each side must be at least {min} m; got {actual} m.",
  "aoiForm.errTooLong": "AOI too large: each side must be at most {max} m; got {actual} m.",
  "aoiForm.errTooLargeArea":
    "AOI too large: area must be at most {max} km²; got {actual} km².",
  "aoiForm.errNoFeatures":
    "This API did not advertise any similarity features for AOI analysis.",

  // AOI workspace
  "aoi.eyebrow": "On-demand workspace",
  "aoi.heading": "New Earth AOI analysis",
  "aoi.intro":
    "Bounded Copernicus DEM terrain metrics plus ESA WorldCover land-cover context. On-demand AOIs are user-requested and are never presented as global coverage.",
  "aoi.idleHeading": "No AOI submitted yet",
  "aoi.idleBody":
    "Use the {tab} tab to submit a bounding box. Limits from GET /capabilities: sides {min}–{max} m, area ≤ {area} km², latitude −80° to 84°, analysis resolution {resolution} m.",
  "aoi.idleHint":
    "A completed AOI can be used as a search target; it also becomes searchable for later searches, with its on-demand origin shown in caveats.",
  "aoi.requestFailed": "AOI request failed",
  "aoi.submittingStatus": "Submitting AOI analysis…",
  "aoi.queuedStatus": "AOI analysis queued…",
  "aoi.runningStatus": "AOI analysis running…",
  "aoi.pollingHint":
    "Job {job} · polling every ~1 s. Provider reads may take several seconds.",
  "aoi.pollFailed": "AOI job polling failed",
  "aoi.retryPolling": "Retry job polling",
  "aoi.featureFailed": "AOI feature record failed to load",
  "aoi.retryFeature": "Retry feature record",
  "aoi.missingAreaId": "The job completed without an area identifier.",
  "aoi.jobStatus": "Job status",
  "aoi.cached": "cached result",
  "aoi.jobId": "Job ID",
  "aoi.areaId": "Area ID",
  "aoi.created": "Created",
  "aoi.finished": "Finished",
  "aoi.failedTitle": "AOI analysis failed",
  "aoi.failedNoMessage": "The job failed without a detailed error message.",
  "aoi.partialTitle": "Partial result",
  "aoi.partialBody":
    "The terrain job finished, but at least one provider did not verify. Inspect the per-provider status before using this record.",
  "aoi.completedHint":
    "All reported providers verified. The result remains a user-requested on-demand AOI, not global coverage.",
  "aoi.perProvider": "Per-provider status",
  "aoi.providerPending": "Provider status is pending.",
  "aoi.reason": "Reason: {reason}",
  "aoi.tile": "Tile: {tile}",
  "aoi.tilesRead": "Tiles read: {tiles}",
  "aoi.validPixels": " · valid pixels: {pixels}",
  "aoi.providerSource": "Provider source",
  "aoi.warnings": "Warnings",
  "aoi.area": "Area",
  "aoi.similarityFeatures": "Similarity features",
  "aoi.contextFeatures": "Context features",
  "aoi.loadingFeatures": "Loading AOI feature record…",
  "aoi.useAsTarget": "Use AOI features as search target",
  "aoi.useAsTargetHint":
    "Maps the AOI's observed terrain values into the manual target form and runs the same search API. The AOI itself remains searchable with its on-demand caveat.",
  "aoi.recordEyebrow": "Completed on-demand record",
  "aoi.onDemandBadge": "On-demand AOI",
  "aoi.compareEyebrow": "Compare",
  "aoi.compareHeading": "Compare this AOI with Earth candidates",
  "aoi.compareIntro":
    "Uses the normal weighted normalized Euclidean comparator with this AOI's own observed terrain values as the target. The call sends no target body and does not introduce a second scoring method. This AOI is itself searchable, so it may appear in its own ranking with distance 0.",
  "aoi.compareButton": "Compare this AOI against indexed Earth candidates",
  "aoi.comparing": "Comparing AOI…",
  "aoi.comparingStatus": "Comparing the AOI against searchable Earth regions…",
  "aoi.compareFailed": "AOI comparison failed.",
  "aoi.compareCode": "(code {code})",
} as const;

export type MessageKey = keyof typeof en;
export type Messages = Record<MessageKey, string>;

/**
 * Display labels for stable API identifiers, keyed by the raw identifier.
 *
 * English labels are empty on purpose: in English the UI shows the API-provided label or
 * the raw identifier, so the API remains the single source of truth for those strings.
 */
export type DisplayLabels = {
  feature: Record<string, string>;
  body: Record<string, string>;
  status: Record<string, string>;
  quality: Record<string, string>;
  dimension: Record<string, string>;
  errorCode: Record<string, string>;
  unitName: Record<string, string>;
  unitSymbol: Record<string, string>;
};

export const enLabels: DisplayLabels = {
  feature: {},
  body: {},
  status: {},
  quality: {},
  dimension: {},
  errorCode: {},
  unitName: {},
  unitSymbol: { degree: "deg", meter: "m" },
};
