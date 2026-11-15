# AnalogX

**Explore the Moon and Mars. Find comparable places on Earth.**

AnalogX is an open-source tool for exploring planetary environments and finding *Earth analogs*—places on Earth that share measurable characteristics with a planetary location. It combines public observations from NASA, USGS, ESA, and other research sources with transparent search methods.

Built for the [NASA Space Apps Challenge 2026](https://www.spaceappschallenge.org/2026/challenges/permanent-moon-base-locations-and-mars/).

[**Try AnalogX**](https://2026-nasa-hackathon.貓熊.台灣/) · [API documentation](https://api.貓熊.台灣/docs) · [繁體中文](README.zh-TW.md)

## What can you do?

1. **Find Earth analogs.** Start with a Moon or Mars reference location, or specify terrain characteristics such as slope and roughness. AnalogX ranks Earth locations from its indexed dataset.
2. **Explore six dimensions of evidence.** Inspect terrain, geology, temperature, water and ice, illumination, and surface materials. Each observation comes with its source and measurement limitations.
3. **Filter the results.** Narrow Earth candidates by terrain ranges and land-cover conditions, such as predominantly bare ground.
4. **Analyze an Earth location.** Select a bounded area and request new terrain measurements, then inspect the result alongside existing candidates.

Similarity rankings use only features that the backend can justify comparing. **Having data for a dimension does not mean that dimension is automatically scored.**

## Six dimensions

| Dimension | What it describes | Current use |
| --- | --- | --- |
| D01 · Terrain | Slope, relief, roughness, flatness | Earth analog ranking |
| D02 · Geology & minerals | Geologic units and mineral evidence | Evidence; not yet a general similarity score |
| D03 · Thermal environment | Temperature and thermal measurements | Context or evidence, depending on source |
| D04 · Water & ice | Water conditions and possible ice indicators | Evidence; proxies are not confirmed ice |
| D05 · Illumination & radiation | Solar conditions and available radiation information | Context; not a radiation-dose comparison |
| D06 · Surface materials | Soil and regolith properties | A validated Earth-only SoilGrids subset can be scored through the multidimensional API |

**Land cover is not D07.** ESA WorldCover is a separate Earth-only filter—for example, to prefer exposed terrain over urban or forested areas.

## What data is available?

The committed research dataset currently includes:

| Dataset | Coverage |
| --- | --- |
| Earth terrain | 113 measured local windows |
| Moon terrain references | 18 windows |
| Mars terrain references | 5 windows |
| Earth coarse discovery index | 168 grid cells across three pilot regions |
| D02–D06 observations | 520 typed records across selected Earth, Moon, and Mars areas |

These are **limited research samples**, not a complete scan of Earth, the Moon, or Mars. A map showing the whole planet does not imply scientific measurements exist everywhere. The backend also exposes verified Moon/Mars basemap metadata for a separate interactive-map frontend under development.

For exact coverage, sources, and quality notes, see [data status](docs/MULTIDIMENSIONAL_RELEASE.md) and [source documentation](docs/DATA_SOURCES.md).

## How matching works

AnalogX compares a chosen target with candidate areas using measured features and explicit weights. Smaller distances indicate closer matches **for the features actually included**; a distance is not a percentage or proof of equivalent planetary conditions.

Missing measurements, incompatible methods, and unsuitable observation scales are identified rather than filled with zero. The multidimensional API can combine selected Earth terrain features with eligible Earth soil-property estimates; it does **not** treat all six dimensions as interchangeable or provide a validated Earth–Moon–Mars six-dimensional score.

[Search method](docs/SEARCH_METHOD.md) · [Multidimensional API](docs/MULTIDIMENSIONAL_SEARCH.md) · [Planetary map API](docs/PLANETARY_MAPS_API.md)

## Run locally

Requirements: [uv](https://docs.astral.sh/uv/), Node.js 22+, and npm.

```bash
git clone https://github.com/ytp-haha-py/2026-NASA-Hackathon.git
cd 2026-NASA-Hackathon
uv sync
make api
```

In a second terminal:

```bash
cd apps/web
npm ci
npm run dev
```

Open `http://localhost:5173`. The API normally runs at `http://127.0.0.1:8000`. The current Vite development proxy targets this local API; the hosted frontend uses the public backend. See [web development](apps/web/README.md) and [deployment](docs/DEPLOYMENT.md).

To run the project checks:

```bash
bash scripts/verify.sh --ci
```

## Architecture and collaboration

- **Web:** React, TypeScript, Vite, Leaflet.
- **API and search:** FastAPI, Python, SQLAlchemy, SQLite.
- **Data processing:** reproducible pipelines, source manifests, Parquet and DuckDB for coarse terrain discovery.
- **Deployment:** Cloudflare Pages for the web; Cloudflare Tunnel to the API host.

Contributions are organized through GitHub Issues and pull requests. Frontend and backend tasks have separate interfaces so they can be developed independently.

[Architecture](docs/ARCHITECTURE.md) · [Data model](docs/DATA_MODEL.md) · [API](docs/API.md) · [Roadmap](docs/ROADMAP.md) · [Contributing](CONTRIBUTING.md)

## License and data attribution

Code is released under the [MIT License](LICENSE). Planetary and Earth datasets retain their respective licenses, attribution requirements, and scientific caveats; see [data sources](docs/DATA_SOURCES.md). AnalogX is a research and exploration tool, **not a verified Moon/Mars base-site recommendation system**.
