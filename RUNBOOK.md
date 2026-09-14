# Runbook: producing libraries for an AOI

The Runbook assumes the twod-fim stack is running with `.env` configured and its database set up; it could be a local, hybrid or cloud stack (see
[README.md](README.md) and the `example*.env` files). Setting that up is not
part of this runbook.

Steps marked **being standardized** work today but will change. Open decisions
are listed at the end.

| Placeholder         | Meaning                                                                            |
| ------------------- | ---------------------------------------------------------------------------------- |
| `<bucket>`          | The artifacts bucket (`ARTIFACTS_S3_BUCKET` in `.env`)                             |
| `<v>`               | The storage generation (`TWOD_FIM_VERSION` in `.env`), e.g. `2026.09`              |
| `<aoi-name>`        | The AOI's name, e.g. `huc6_120401`, for its file names and record folder           |
| `<workdir>`         | A local working folder for modifying the network                                   |
| `<aoi-config-path>` | The AOI config the commands are given (step 3), a local path or an `s3://` address |
| `<out-dir>`         | A local folder the flows2fim outputs are written to (step 8)                       |
| `<sepex-url>`       | SEPEX's address (`SEPEX_URL` in `.env`)                                            |

## Where things live

| Location                          | Holds                                                                                                | Written by                                                       | Read by                                                             |
| --------------------------------- | ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- | ------------------------------------------------------------------- |
| `source_data/`                    | External source data, any number of variants side by side: CONUS or regional hydrofabric, coastal influence polygons, DEMs, land-cover rasters and lookups, flow statistics | People staging data. Never changed; new data is added beside it. | Seeding, and jobs through the sources intent names                  |
| `version=<v>/workspace/`          | The system's working data: `reach_network.parquet`, `lakes/`, `coasts/`                              | Seeding                                                          | Jobs. **Not scratch space:** removing a file breaks work in flight. |
| `version=<v>/provenance/`         | Copies of what produced this generation, kept for the record, one folder per AOI: `aois/<aoi-name>/aoi_config.jsonc`, and `aois/<aoi-name>/networks/<identity_hash>/` for `modify_network` output | People (step 9)                                                  | People. Nothing in the system reads it.                             |
| `version=<v>/models/`, `results/` | Materialized outputs                                                                                 | Jobs                                                             | The loop, flows2fim                                                 |

All are under `s3://<bucket>/`. `source_data/` sits at the bucket root, outside
every version's outputs, because source data has nothing to do with
versioning.

## 1. Source data

Make sure what your AOI will need as source data (a custom DEM, specific land cover, etc.) is under `source_data/`. You can use `just stage-source-data <file> <path-in-source_data>` to upload files to source data. If you don't have any custom data and want to use the default datasets everywhere, copy the default datasets from `s3://fimc-data/twod-fim/source_data/` into your `s3://<bucket>/source_data/`
.

## 2. Modify the network

Runs the `modify_network` job from `twod-fim-jobs` with Docker, on local files.

1. **Download the base data** if you don't have it (starting NHF and Coastal Influence layers) into `<workdir>`. The following commands assume these files are in `source_data` too.

   ```bash
   aws s3 cp s3://<bucket>/source_data/hydrofabric/nhf.gpkg <workdir>/
   aws s3 cp s3://<bucket>/source_data/coastal_influence/coastal_influence.gpkg <workdir>/
   ```
1. **Subset the NHF** to the AOI as `<workdir>/<aoi-name>.gpkg`, or keep it as is if your AOI is the whole of CONUS.
1. **Run the `modify_network` job**:

   ```bash
   docker run --rm -v <workdir>:/data ghcr.io/ngwpc/twod-fim-jobs/modify_network:<tag> '{
     "reach_network_path": "/data/<aoi-name>.gpkg",
     "lakes_layer_path": "/data/<aoi-name>.gpkg",
     "coastal_influence_layer_path": "/data/coastal_influence.gpkg",
     "base_output_path": "/data",
     "stream_order_filter_threshold": 3
   }'
   ```

   The output lands in `<workdir>/<identity_hash>/`: `network.gpkg`, `lakes.gpkg` and `network.json`. The hash covers the inputs and parameters, so running again with the same ones reports that the network exists and writes nothing. The AOI config names this output where it is.

## 3. Write the AOI config

An AOI config is the payload for the seed and author commands: it names everything they read. Write it anywhere, for example `<workdir>/aoi_config.jsonc`, and give each command its path. [example.aoi_config.jsonc](example.aoi_config.jsonc) lists every option in one file.

- **Locations** are local paths or `s3://` addresses.
    - **`{source_data}`** stands for `s3://<bucket>/source_data`, taken from `.env`, so the file names no bucket.
    - **A relative path** is relative to the AOI config itself. Full paths mean the same thing wherever a copy of the file ends up.
- **A typo in a key name** is reported, not ignored.

Each of the following steps starts with the part of the AOI config it uses and the options there. Optional keys are shown commented out, with placeholder values. An AOI config only overrides: a source it leaves out (`dem_source`, `lulc_source`, `lulc_lookup`) comes from the database's own `desired_state_defaults`, and flow statistics it leaves out come from the system-wide settings. Set one only to give this AOI something different: naming the default's value still pins these reaches to it, so they would no longer follow a later change of the default.

## 4. Seed lakes and coasts

**AOI config:**

```jsonc
{
  // seed-lakes: a GeoPackage with layer lakes_polygons, keyed by lake_id.
  // Left out, seed-lakes stops.
  "lakes": "{source_data}/hydrofabric/nhf_v1.2.3.gpkg",
  // seed-coasts: a GeoPackage with layer coastal_influence_polygons, keyed by
  // coast_id. Left out, seed-coasts stops.
  "coasts": "{source_data}/coastal_influence/coastal_influence.gpkg"
}
```

Seed all lakes and coasts in your AOI if you have custom lakes or coasts, or this is the first AOI against this database. This is not required if an earlier seeding has already seeded CONUS-wide lakes and coasts and there are no custom lakes and coasts in your data.\
If files are on cloud, they will be downloaded locally for processing. Local files can also be used.

```bash
just seed-lakes  <aoi-config-path>
just seed-coasts <aoi-config-path>
```

- **What they load:** every lake in `lakes` and every polygon in `coasts` go into the database. Each one is also published to `workspace/lakes/<lake_id>.geojson` or `workspace/coasts/<coast_id>.geojson`, the outflow area a terminal reach's nd job reads.
- **They're whole datasets,** not tied to one AOI, so later AOIs in the same storage generation can skip this step.
- **They add or update, never delete,** so running them again is safe. The full `nhf.gpkg` is a 1.4 GiB download.

## 5. Seed the network

**AOI config:**

```jsonc
{
  // modify_network's network.gpkg from step 2 (layer reach_network).
  // Left out, seed-network stops.
  "network": "<workdir>/<identity_hash>/network.gpkg"
}
```

This is required to populate the database with the modified reach network from step 2. This network is added to the network the database already has.

```bash
just seed-network <aoi-config-path>
```

- **Loads** the AOI's reaches into the database, and publishes the database's whole network to `workspace/reach_network.parquet`, which `build_model` reads.
- **Checks first** that every lake and coast the network names is already seeded. If one isn't, it stops, names it, and says which seed step to run.
- **Adds or updates, never deletes.** Several AOIs can live in one database. For a clean database, run `just wipe-db` and start the stack again.
- **Check:** the summary shows reach and terminal counts (lake, coast, outlet). Outlet terminals need no water body.

## 6. Author intent

**AOI config:**

```jsonc
{
  // The network seeded in step 5. Only its reaches are authored. Left out,
  // author-intent stops.
  "network": "<workdir>/<identity_hash>/network.gpkg"

  // Per-reach flows, .parquet or .csv. Only the network's reaches it covers are
  // authored, so a smaller file limits a test run to fewer reaches. Left out,
  // the system-wide flow statistics are used.
  // "flow_statistics": "{source_data}/<flows>.parquet",

  // What flow_statistics calls the reach id (a column or the index), and the two
  // columns the discharge bounds come from (DR-029: high flow threshold to the
  // 100-year discharge). Left out, the system-wide column names are used.
  // "flow_reach_id_column": "<reach-id-column>",
  // "flow_q_lower_column": "<high-flow-threshold-column>",
  // "flow_q_upper_column": "<100-year-discharge-column>",

  // Where jobs read elevation and land cover: s3://, https:// or /vsi..., never
  // a local path. Left out, these reaches follow the database's
  // desired_state_defaults. Part of model identity, so changing one rebuilds
  // these reaches' models.
  // "dem_source": "{source_data}/<dem>.tif",
  // "lulc_source": "{source_data}/<land-cover>.tif",

  // The land-cover to Manning's n JSON. Must be s3:// (the loop reads it too) and
  // must already exist. Left out, these reaches follow the database's
  // desired_state_defaults. Identity hashes its content, so editing the file
  // rebuilds every model that uses it.
  // "lulc_lookup": "{source_data}/<lookup>.json",

  // [lower, upper]: pull each reach's discharge bounds inward to keep a test run
  // short, lower >= 1 raising the floor and 0 < upper <= 1 lowering the ceiling.
  // Left out, the bounds are DR-029's full range, which is what a real AOI wants.
  // "q_bound_factors": [1.3, 0.7]
}
```

Populating the database with a reach network only tells the system these reaches exist. To tell the system what is desired for these reaches, we must author intent for them.

```bash
just author-intent <aoi-config-path>
```

- **Per reach:** writes `desired_state` for the reaches of the AOI's own `network` that the flow statistics cover, so AOIs sharing a database don't author over each other. Discharge bounds come from those statistics (DR-029), placed on a discharge grid (DR-041), with the AOI's own sources when it names them. Reaches the statistics don't cover aren't authored, and the report counts them.
- **Checks first:** the defaults exist (written when the database was set up), the network is seeded, what's authored is downstream-closed, and the land-cover lookup these reaches use is readable: the AOI's own, or the default in force.
- **Adds or updates, never deletes.** Running again with nothing changed bumps no revisions, so nothing rebuilds.

## 7. Run the loop (if not running already)

**AOI config:** not read. The loop works from what steps 4–6 wrote to the database.

Authoring intent tells the system what we desire; running the reconciliation loop makes the system work towards that intent. In production the loop is always running, so step 6 is enough.

```bash
just reconcile
```

It exits once the AOI settles. To keep it running instead: `cd orchestrator && uv run python scripts/reconcile.py --forever`.

**Watch progress:**

```bash
curl -s "<sepex-url>/jobs?f=json&limit=20"
aws s3 ls s3://<bucket>/version=<v>/results/ --recursive | wc -l
```

**What to expect:**

- **`build_model`** runs as a Docker job wherever SEPEX runs.
- **nd and kwse** run on Batch GPU instances in the cloud setup. After the queue has been idle, the first job takes about 2–3 minutes to reach `RUNNING` while an instance starts and pulls its image.
- **SEPEX shows `accepted`** until Batch reports `RUNNING`. Status reaches SEPEX through the Batch → EventBridge → Lambda callback.
- **A failure halts that reach** (`HALT_AFTER_FAILURES=1`). The loop's output names the SEPEX job, and `<sepex-url>/jobs/<jobID>/logs` has its logs.

## 8. Publish for flows2fim

**AOI config:**

```jsonc
{
  // Only the network's reaches are exported, and only those materialized so far.
  // Left out, f2f stops.
  "network": "<workdir>/<identity_hash>/network.gpkg"

  // The flow statistics the AEP flows are read from, and what they call the
  // reach id. Left out, the system-wide flow statistics and column name are used,
  // as in step 6.
  // "flow_statistics": "{source_data}/<flows>.parquet",
  // "flow_reach_id_column": "<reach-id-column>",

  // The columns forecast as AEP flows, one set of flows2fim controls and one
  // depth VRT each. Left out, the system-wide AEP columns are used.
  // "flow_aep_columns": ["<aep-column>", "<aep-column>"]
}
```

twod-fim outputs and flows2fim are not yet directly compatible, so the outputs have to be adapted for flows2fim. The following command does that, and also creates sample AEP grids for the AOI.

```bash
just f2f <aoi-config-path> <out-dir>
```

It runs three steps, each also a command of `orchestrator/scripts/f2f.py`:

- **`scenarios`** writes `<out-dir>/scenarios.db`, the tables flows2fim reads, for the AOI's materialized reaches, and `<out-dir>/start_reaches.csv`, the reaches flows2fim starts from. The report counts reaches not materialized yet. Start reaches are the ones with nowhere left to drain in the export, each at normal depth: true terminals, and, as a fallback, reaches whose downstream neighbour isn't exported.
- **`library`** downloads the depth grids `scenarios.db` names to `<out-dir>/library/`. Running it again downloads only what changed; grids no longer named are reported, and removed with `--prune`.
- **`aep`** writes `<out-dir>/aep/<column>/`: `flows.csv`, flows2fim's `controls.csv` (started from `start_reaches.csv`), and `depth.vrt`, for each AEP column. Reaches without a flow in a column are left out of that forecast and counted. flows2fim runs in Docker.

Several AOIs are several out-dirs. See `orchestrator/README.md`, section 5, for why the export follows the database rather than storage.

## 9. Keep a record (optional)

**AOI config:** the whole file, copied as it is.

Optionally upload your AOI config and its related files to S3 to preserve them for later.

```bash
aws s3 cp <aoi-config-path> s3://<bucket>/version=<v>/provenance/aois/<aoi-name>/aoi_config.jsonc
aws s3 cp <workdir>/<identity_hash>/ s3://<bucket>/version=<v>/provenance/aois/<aoi-name>/networks/<identity_hash>/ --recursive
```

## Open decisions

- **`bound_flows.py`:** turn it into a tool like the seed, author and f2f scripts, with arguments instead of fixed paths.
- **NHF subsetting:** the tool or command for step 2.2.