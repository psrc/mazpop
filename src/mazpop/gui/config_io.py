"""Config loading helpers shared by the editor tabs.

A mazpop project configures two population synthesis runs — one for
``history_year`` (land use calibration) and one for ``base_year`` — so every
lookup here takes a ``year_key`` (``"base"`` or ``"history"``) and resolves:

* which package config CSVs describe that year's ACS and decennial data,
* the project file ``configs/<year_key>_marginals_groups.yaml``,
* the project dir ``popsim_<year_key>_year/configs/`` that holds controls.csv.

Keeping this in one place is what lets the Marginals Groups and Controls tabs
agree on groups, bins, and seed expressions for the year being edited.
"""

from collections import OrderedDict
from pathlib import Path

import pandas as pd
import yaml

from mazpop.gui import projects
from mazpop.util.pipeline import Pipeline

# Settings keys use these exact prefixes (base_year/base_acs_year, ...).
YEAR_KEYS = ("base", "history")

_YEAR_LABELS = {"base": "Base", "history": "History"}


# ---------------------------------------------------------------------------
# settings / year resolution
# ---------------------------------------------------------------------------


def year_options(project_dir) -> list[tuple[str, int]]:
    """Return the configured ``(year_key, year)`` pairs, base first."""
    settings = projects.load_settings(project_dir)
    out = []
    for year_key in YEAR_KEYS:
        year = settings.get(f"{year_key}_year")
        if year is not None:
            out.append((year_key, int(year)))
    return out


def year_label(project_dir, year_key) -> str:
    """Return a human readable label like ``Base year (2020)``."""
    settings = projects.load_settings(project_dir)
    year = settings.get(f"{year_key}_year", "?")
    return f"{_YEAR_LABELS.get(year_key, year_key)} year ({year})"


def year_context(project_dir, year_key) -> dict:
    """Build the Pipeline context for one of a project's two runs."""
    settings = projects.load_settings(project_dir)
    return {
        "configs_dir": str(projects.configs_dir(project_dir)),
        "year_key": year_key,
        "year": settings.get(f"{year_key}_year"),
        "acs_year": settings.get(f"{year_key}_acs_year"),
    }


def get_pipeline(project_dir, year_key) -> Pipeline:
    """Return a Pipeline for a project/year pair.

    Raises ``FileNotFoundError`` when settings.yaml is missing and
    ``ValueError`` when required settings (e.g. ``census_key``) are absent.
    """
    return Pipeline(year_context(project_dir, year_key))


def get_config_dirs(project_dir, year_key) -> tuple[Path, Path]:
    """Return ``(acs_config_dir, dec_config_dir)`` for a year key.

    Resolution goes through ``Pipeline`` so the GUI uses exactly the same
    package configs (including the fall-back-to-latest-year behaviour) as the
    pipeline itself.
    """
    pipeline = get_pipeline(project_dir, year_key)
    return (
        Path(pipeline.get_acs_config_dir(pipeline.context["acs_year"])),
        Path(pipeline.get_dec_config_dir(pipeline.context["year"])),
    )


# ---------------------------------------------------------------------------
# project-side paths
# ---------------------------------------------------------------------------


def marginals_groups_path(project_dir, year_key) -> Path:
    """Return ``configs/<year_key>_marginals_groups.yaml``."""
    return projects.configs_dir(project_dir) / f"{year_key}_marginals_groups.yaml"


def popsim_configs_dir(project_dir, year_key) -> Path:
    """Return the popsim ``configs`` dir for a year key."""
    return Path(project_dir) / f"popsim_{year_key}_year" / "configs"


def controls_path(project_dir, year_key) -> Path:
    """Return the ``controls.csv`` path for a year key."""
    return popsim_configs_dir(project_dir, year_key) / "controls.csv"


# ---------------------------------------------------------------------------
# package config CSVs
# ---------------------------------------------------------------------------


def read_config_csv(path) -> pd.DataFrame:
    """Read a package config CSV as strings, with blanks instead of NaN."""
    return pd.read_csv(path, dtype=str).fillna("")


def _always_download_groups(df: pd.DataFrame) -> set[str]:
    if "always_download" not in df.columns:
        return set()
    return {
        str(row["group"])
        for _, row in df.iterrows()
        if str(row.get("always_download", "")).strip().upper() == "TRUE"
    }


def load_group_names(
    marginals_csv, exclude_always_download: bool = True
) -> "OrderedDict[str, list[str]]":
    """Return ``{group: [name, ...]}`` from a marginals expressions CSV.

    ``always_download`` groups (e.g. ``total_pop_age``, ``tenure``) are
    downloaded 1:1 by the pipeline regardless of the groups YAML, so they are
    not binnable; they are excluded unless ``exclude_always_download`` is
    False.
    """
    path = Path(marginals_csv)
    if not path.exists():
        return OrderedDict()
    df = read_config_csv(path)
    always = _always_download_groups(df)
    groups: "OrderedDict[str, list[str]]" = OrderedDict()
    for _, row in df.iterrows():
        group = str(row["group"])
        if exclude_always_download and group in always:
            continue
        groups.setdefault(group, []).append(str(row["name"]))
    return groups


def acs_marginals_csv(acs_dir) -> Path:
    return Path(acs_dir) / "marginals_expressions.csv"


def dec_marginals_csv(dec_dir) -> Path:
    return Path(dec_dir) / "block_marginals_expressions.csv"


def binnable_group_columns(acs_dir, dec_dir) -> "OrderedDict[str, list[str]]":
    """Groups the Marginals Groups tab can aggregate: ACS groups, then decennial.

    Decennial groups are appended (and win on a name clash) exactly like the
    pipeline's ``build_aggregated_variables_by_group`` expects.
    """
    groups = load_group_names(acs_marginals_csv(acs_dir))
    groups.update(load_group_names(dec_marginals_csv(dec_dir)))
    return groups


def all_dec_group_columns(dec_dir) -> "OrderedDict[str, list[str]]":
    """Every decennial group, including always-download ones (e.g. tenure)."""
    return load_group_names(dec_marginals_csv(dec_dir), exclude_always_download=False)


def load_marginals_expr(acs_dir, dec_dir) -> dict[tuple[str, str], str]:
    """Return ``{(group, name): seed_expression}`` for ACS and decennial rows."""
    out: dict[tuple[str, str], str] = {}
    for path in (acs_marginals_csv(acs_dir), dec_marginals_csv(dec_dir)):
        if not Path(path).exists():
            continue
        df = read_config_csv(path)
        for _, row in df.iterrows():
            out[(str(row["group"]), str(row["name"]))] = str(
                row.get("seed_expression", "")
            )
    return out


def load_total_variables(acs_dir, dec_dir) -> list[tuple[str, str, str]]:
    """Return ``[(name, source, seed_expression), ...]`` for all total variables."""
    out: list[tuple[str, str, str]] = []
    for path, source in (
        (Path(acs_dir) / "total_variables.csv", "acs"),
        (Path(dec_dir) / "total_variables.csv", "dec"),
    ):
        if not path.exists():
            continue
        df = read_config_csv(path)
        for _, row in df.iterrows():
            out.append((str(row["name"]), source, str(row.get("seed_expression", ""))))
    return out


# ---------------------------------------------------------------------------
# project marginals groups
# ---------------------------------------------------------------------------


def load_marginals_groups(
    project_dir, year_key
) -> "OrderedDict[str, list[tuple[str, list[str]]]]":
    """Read ``configs/<year_key>_marginals_groups.yaml``.

    Returns ``{group: [(bin_name, [component, ...]), ...]}``; a missing or
    empty file yields an empty OrderedDict.
    """
    path = marginals_groups_path(project_dir, year_key)
    if not path.exists():
        return OrderedDict()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: "OrderedDict[str, list[tuple[str, list[str]]]]" = OrderedDict()
    for group, bins in raw.items():
        if bins is None:
            continue
        bin_list: list[tuple[str, list[str]]] = []
        for item in bins:
            if isinstance(item, dict):
                for k, v in item.items():
                    components = [str(s) for s in (v if isinstance(v, list) else [v])]
                    bin_list.append((str(k), components))
        out[str(group)] = bin_list
    return out


def control_groups(
    project_dir, year_key
) -> "OrderedDict[str, list[tuple[str, list[str], str]]]":
    """Groups offered in the Controls tab: ``{group: [(bin, comps, source)]}``.

    The groups YAML comes first (ACS or decennial source inferred per group),
    followed by any decennial group missing from the YAML (e.g. ``tenure``,
    which the pipeline always downloads) added 1:1, mirroring how
    ``build_aggregated_variables_by_group`` merges those groups.
    """
    yaml_groups = load_marginals_groups(project_dir, year_key)
    _, dec_dir = get_config_dirs(project_dir, year_key)
    dec_groups = all_dec_group_columns(dec_dir)

    out: "OrderedDict[str, list[tuple[str, list[str], str]]]" = OrderedDict()
    for group, bins in yaml_groups.items():
        source = "dec" if group in dec_groups else "acs"
        out[group] = [(name, comps, source) for name, comps in bins]

    for group, names in dec_groups.items():
        if group not in out:
            out[group] = [(name, [name], "dec") for name in names]
    return out


def geographies_for_source(source: str) -> list[str]:
    """Geography options for a control: decennial groups can target blocks."""
    return ["block_id", "tract_id", "region"] if source == "dec" else ["tract_id", "region"]
