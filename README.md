# mazpop

PSRC's MAZ-level population synthesis pipeline.

## Setup

```bash
uv sync
```

## Projects

A project folder (for example `projects/example_project`) holds everything needed
for one regional run:

| Path | Purpose |
| --- | --- |
| `configs/settings.yaml` | years, census key name, spatial layers, POI layers, pipeline steps |
| `configs/state_county_fips.yaml` | counties to download data for |
| `configs/base_marginals_groups.yaml` | marginals groups/bins for the **base year** run |
| `configs/history_marginals_groups.yaml` | marginals groups/bins for the **history year** run |
| `popsim_base_year/configs/controls.csv` | controls for the base year run |
| `popsim_history_year/configs/controls.csv` | controls for the history year run |
| `popsim_<year>/{configs,data,output}/` | PopulationSim inputs (seed data, marginals) and outputs |
| `output/pipeline/` | intermediate tables written by the pipeline steps |

Each project configures **two** population synthesis runs: a `history_year` run
used for land use model calibration and a `base_year` run that produces the
synthetic population.

## Editor GUI

```bash
uv run mazpop editor                 # or: uv run mazpop editor --project example_project
```

Streamlit tabs:

* **Projects** — create a project (copied from `projects/default_template`) or
  open an existing one.
* **Census API Key** — store the key in the project's git-ignored `.env` under
  the environment-variable name recorded in `settings.yaml`. `mazpop synthesize`
  reads that file, so the key does not have to be exported in the shell.
* **County Selection** — pick states/counties; saved to
  `configs/state_county_fips.yaml`.
* **Marginals Groups** — choose groups and build custom bins; saved to
  `configs/<year_key>_marginals_groups.yaml`.
* **Controls** — pick a geography and importance per group (plus optional
  individual totals); saved to `popsim_<year_key>_year/configs/controls.csv`.

The sidebar **Synthesis year** selector chooses which run the Marginals Groups
and Controls tabs edit, so both files are written for `base` and `history`.

The projects directory is `<cwd>/projects` (falling back to the repo's
`projects/`), or `$MAZPOP_PROJECTS_DIR` when set.

## Run the pipeline

```bash
uv run mazpop synthesize -c projects/example_project/configs
```

This runs the pypyr pipeline in `configs/settings.yaml`, which includes
PopulationSim for both the history year and the base year, then
post-processing, calibration exports, and jobs/POI processing.
