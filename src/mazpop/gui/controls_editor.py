"""Controls editor tab.

Turns the year's marginals groups (``configs/<year_key>_marginals_groups.yaml``)
plus the package config CSVs into a PopulationSim ``controls.csv`` written to
``popsim_<year_key>_year/configs/controls.csv`` — the file both
``mazpop.steps.create_marginals`` and PopulationSim read for that run.

For each marginal group the user picks a geography and an importance; every bin
in the group shares them.  Bins with several components get a combined seed
expression built by OR-combining the source row expressions from the marginals
expressions CSVs.

Two controls are always emitted: ``num_units`` at blocks (the total household
control PopulationSim needs) and ``num_persons`` at the region.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from mazpop.gui import config_io

SESSION_PREFIXES = ("ctrl_",)

# Standalone (non-group) controls always written, using the pipeline's
# special-control aliases from total_variables.csv.
_AUTO_ROWS = [
    {
        "target": "num_units",
        "geography": "block_id",
        "seed_table": "households",
        "importance": 1_000_000_000,
        "control_field": "num_units",
        "expression": "(households.wgtp > 0) & (households.wgtp < np.inf)",
    },
    {
        "target": "num_persons",
        "geography": "region",
        "seed_table": "persons",
        "importance": 10_000,
        "control_field": "num_persons",
        "expression": "persons.hh_id > 0",
    },
]
_AUTO_TARGETS = {row["target"] for row in _AUTO_ROWS}

_IMPORTANCE_MAX = 10_000
_IMPORTANCE_STEP = 100


# ---------------------------------------------------------------------------
# session-state keys (all prefixed with 'ctrl_')
# ---------------------------------------------------------------------------


def _loaded_key(year_key: str) -> str:
    return f"ctrl_loaded__{year_key}"


def _geog_key(year_key: str, group: str) -> str:
    return f"ctrl_geog__{year_key}__{group}"


def _imp_key(year_key: str, group: str) -> str:
    return f"ctrl_imp__{year_key}__{group}"


def _var_geog_key(year_key: str, source: str, name: str) -> str:
    return f"ctrl_var_geog__{year_key}__{source}__{name}"


def _var_imp_key(year_key: str, source: str, name: str) -> str:
    return f"ctrl_var_imp__{year_key}__{source}__{name}"


def _individual_key(year_key: str) -> str:
    return f"ctrl_individual__{year_key}"


def _defaults(key: str, **kwargs) -> dict:
    """Return widget kwargs, but only when the key isn't already set."""
    return {} if key in st.session_state else kwargs


def _clamp_importance(value) -> int:
    """Coerce a stored importance into the slider's range."""
    try:
        importance = int(value)
    except (TypeError, ValueError):
        return 1000
    importance = max(0, min(importance, _IMPORTANCE_MAX))
    return importance - (importance % _IMPORTANCE_STEP)


# ---------------------------------------------------------------------------
# expression helpers
# ---------------------------------------------------------------------------


def combine_seed_expressions(expressions: list[str]) -> str:
    """OR-combine component seed expressions into one bin expression."""
    cleaned = [e.strip() for e in expressions if e and str(e).strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return " | ".join(f"({e})" for e in cleaned)


def seed_table_from_expr(expr: str) -> str:
    """Return the seed table ('households'/'persons') an expression targets."""
    if not expr:
        return ""
    return expr.strip().lstrip("(").split(".", 1)[0].strip()


# ---------------------------------------------------------------------------
# controls.csv builder
# ---------------------------------------------------------------------------


def build_controls_rows(
    group_bins: dict,
    group_choices: dict,
    individual_choices: list[dict],
    expressions: dict[tuple[str, str], str],
) -> tuple[pd.DataFrame, list[str]]:
    """Build the controls DataFrame and return ``(df, warnings)``."""
    rows = [dict(row) for row in _AUTO_ROWS]
    warnings: list[str] = []

    for group, bins in group_bins.items():
        choice = group_choices.get(group)
        if choice is None:
            continue
        geography = choice["geography"]
        importance = int(choice["importance"])
        for bin_name, components, _source in bins:
            combined = combine_seed_expressions(
                [expressions.get((group, c), "") for c in components]
            )
            if not combined:
                warnings.append(
                    f"Skipped {group}_{bin_name}: no seed expression found for "
                    f"components {components}."
                )
                continue
            target = f"{group}_{bin_name}"
            rows.append(
                {
                    "target": target,
                    "geography": geography,
                    "seed_table": seed_table_from_expr(combined),
                    "importance": importance,
                    "control_field": target,
                    "expression": combined,
                }
            )

    for choice in individual_choices:
        expr = (choice.get("expression") or "").strip()
        if not expr:
            warnings.append(
                f"Skipped individual variable '{choice['name']} "
                f"({choice['source']})': no seed expression defined."
            )
            continue
        rows.append(
            {
                "target": choice["name"],
                "geography": choice["geography"],
                "seed_table": seed_table_from_expr(expr),
                "importance": int(choice["importance"]),
                "control_field": choice["name"],
                "expression": expr,
            }
        )

    df = pd.DataFrame(
        rows,
        columns=[
            "target",
            "geography",
            "seed_table",
            "importance",
            "control_field",
            "expression",
        ],
    )
    return df, warnings


# ---------------------------------------------------------------------------
# loading an existing controls.csv
# ---------------------------------------------------------------------------


def _match_group(target: str, group_names: list[str]) -> str | None:
    """Return the longest group name that is a ``{group}_`` prefix of target."""
    candidates = [g for g in group_names if target.startswith(f"{g}_")]
    if not candidates:
        return None
    return max(candidates, key=len)


def _auto_load_controls(
    controls_path: Path,
    year_key: str,
    group_bins: dict,
    totals: list[tuple[str, str, str]],
):
    """Populate session state from an existing controls.csv (once per year)."""
    if st.session_state.get(_loaded_key(year_key)):
        return
    st.session_state[_loaded_key(year_key)] = True
    if not controls_path.exists():
        return

    try:
        df = pd.read_csv(controls_path, dtype=str).fillna("")
    except Exception:  # noqa: BLE001 - an unreadable file is not fatal here
        return
    if not {"target", "geography", "importance"}.issubset(df.columns):
        return

    group_sources = {
        group: (bins[0][2] if bins else "acs") for group, bins in group_bins.items()
    }
    group_names = list(group_bins)
    totals_by_name = {name: source for name, source, _ in totals}

    individual_labels: list[str] = []
    seen_individuals: set[str] = set()

    for _, row in df.iterrows():
        target = str(row["target"])
        if target in _AUTO_TARGETS:
            continue
        geography = str(row["geography"])
        importance = _clamp_importance(row["importance"])

        group = _match_group(target, group_names)
        if group is not None:
            options = config_io.geographies_for_source(group_sources[group])
            if geography in options:
                st.session_state.setdefault(_geog_key(year_key, group), geography)
            st.session_state.setdefault(_imp_key(year_key, group), importance)
            continue

        if target in totals_by_name:
            source = totals_by_name[target]
            label = f"{target} ({source})"
            if target not in seen_individuals:
                seen_individuals.add(target)
                individual_labels.append(label)
            options = config_io.geographies_for_source(source)
            key = _var_geog_key(year_key, source, target)
            if geography in options:
                st.session_state.setdefault(key, geography)
            st.session_state.setdefault(_var_imp_key(year_key, source, target), importance)

    if individual_labels:
        st.session_state.setdefault(_individual_key(year_key), individual_labels)


# ---------------------------------------------------------------------------
# widget helpers
# ---------------------------------------------------------------------------


def _importance_slider(key: str) -> int:
    return st.slider(
        "Importance",
        min_value=0,
        max_value=_IMPORTANCE_MAX,
        step=_IMPORTANCE_STEP,
        key=key,
        **_defaults(key, value=1000),
    )


def _geography_selectbox(key: str, options: list[str], default_index: int = 0) -> str:
    return st.selectbox(
        "Geography",
        options=options,
        key=key,
        **_defaults(key, index=default_index),
    )


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def render(project_dir: Path, year_key: str):
    """Render the Controls tab for one of the project's two runs."""
    try:
        group_bins = config_io.control_groups(project_dir, year_key)
        acs_dir, dec_dir = config_io.get_config_dirs(project_dir, year_key)
        expressions = config_io.load_marginals_expr(acs_dir, dec_dir)
        totals = config_io.load_total_variables(acs_dir, dec_dir)
    except Exception as exc:
        st.error(f"Could not load the configs for this year: {exc}")
        return

    if not group_bins:
        st.warning(
            "No marginal groups found for this year. Configure them in the "
            "'Marginals Groups' tab first."
        )
        return

    out_path = config_io.controls_path(project_dir, year_key)
    st.caption(f"Writing `{out_path.relative_to(Path(project_dir))}`")

    _auto_load_controls(out_path, year_key, group_bins, totals)

    # --- marginal groups -----------------------------------------------------
    st.subheader("Marginal Groups")
    st.caption(
        "Pick a geography and importance for each group. All bins within a "
        "group share the same geography and importance."
    )

    group_choices: dict[str, dict] = {}
    for group, bins in group_bins.items():
        source = bins[0][2] if bins else "acs"
        with st.expander(
            f"**{group}** ({len(bins)} bin{'s' if len(bins) != 1 else ''})",
            expanded=True,
        ):
            st.caption("Bins: " + ", ".join(f"`{b[0]}`" for b in bins))
            geography = _geography_selectbox(
                _geog_key(year_key, group), config_io.geographies_for_source(source)
            )
            importance = _importance_slider(_imp_key(year_key, group))
            group_choices[group] = {"geography": geography, "importance": importance}

    # --- individual variables ------------------------------------------------
    st.subheader("Individual Variables")
    st.caption("Optional. Add individual total variables from the package configs.")

    label_to_total = {
        f"{name} ({source})": (name, source, expr) for name, source, expr in totals
    }
    selected_labels = st.multiselect(
        "Variables",
        options=list(label_to_total.keys()),
        key=_individual_key(year_key),
    )

    individual_choices: list[dict] = []
    for label in selected_labels:
        name, source, expr = label_to_total[label]
        with st.expander(f"**{label}**", expanded=True):
            if not expr:
                st.warning(
                    "No seed expression defined for this variable in "
                    "total_variables.csv. It will be skipped on save."
                )
            geography = _geography_selectbox(
                _var_geog_key(year_key, source, name),
                config_io.geographies_for_source(source),
            )
            importance = _importance_slider(_var_imp_key(year_key, source, name))
            individual_choices.append(
                {
                    "name": name,
                    "source": source,
                    "expression": expr,
                    "geography": geography,
                    "importance": importance,
                }
            )

    # --- save ----------------------------------------------------------------
    st.divider()
    df, warnings = build_controls_rows(
        group_bins, group_choices, individual_choices, expressions
    )
    for warning in warnings:
        st.warning(f"⚠️ {warning}")

    if st.button("💾 Save controls.csv", type="primary", key=f"ctrl_save__{year_key}"):
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(out_path, index=False)
        except Exception as exc:  # noqa: BLE001 - surface any write failure
            st.error(f"Failed to save: {exc}")
            return
        st.success(f"Wrote {len(df)} rows to {out_path}")

    with st.expander("Preview", expanded=False):
        st.dataframe(df, width="stretch", hide_index=True)
