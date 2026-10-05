"""County Selection tab for the project GUI.

Reads ``state_county_fips.csv`` from the package configs and lets the user pick
states and counties with multiselect widgets.  Selections are saved to the
project's ``configs/state_county_fips.yaml`` as a flat list of FIPS codes,
which the pipeline reads for both the history and base year runs.
"""

from importlib import resources
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

SESSION_PREFIXES = ("county_",)


def _fips_lookup_path() -> Path:
    return Path(str(resources.files("mazpop.configs"))) / "state_county_fips.csv"


def _fips_path(project_dir: Path) -> Path:
    return Path(project_dir) / "configs" / "state_county_fips.yaml"


def _load_fips_lookup() -> pd.DataFrame:
    return pd.read_csv(_fips_lookup_path())


def _load_fips_codes(project_dir: Path) -> list[int]:
    path = _fips_path(project_dir)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, list):
            return data
    return []


def _save_fips_codes(project_dir: Path, fips_codes: list[int]):
    path = _fips_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(fips_codes, f, default_flow_style=True)


def render(project_dir: Path, year_key: str):
    st.header("County Selection")
    st.caption(
        "Counties drive data downloads for both the base year and history year runs."
    )

    if not _fips_lookup_path().exists():
        st.error(f"Cannot find state_county_fips.csv at {_fips_lookup_path()}")
        return

    fips_df = _load_fips_lookup()
    current_codes = _load_fips_codes(project_dir)

    all_states = sorted(fips_df["state"].unique().tolist())
    saved_states = (
        fips_df.loc[fips_df["state_county_fips"].isin(current_codes), "state"]
        .unique()
        .tolist()
    )

    if "county_sel_states" not in st.session_state:
        st.session_state.county_sel_states = saved_states

    # --- add-state control ---------------------------------------------------
    available_to_add = [s for s in all_states if s not in st.session_state.county_sel_states]
    col_add, col_btn = st.columns([3, 1])
    with col_add:
        new_state = st.selectbox(
            "Add a state",
            options=[""] + available_to_add,
            index=0,
            label_visibility="collapsed",
            placeholder="Add a state…",
            key="county_new_state",
        )
    with col_btn:
        if st.button("Add State") and new_state:
            st.session_state.county_sel_states.append(new_state)
            st.rerun()

    # --- per-state county selectors ------------------------------------------
    selected_codes: list[int] = []

    for state_name in list(st.session_state.county_sel_states):
        state_counties = fips_df.loc[fips_df["state"] == state_name]
        county_options = sorted(state_counties["county"].tolist())
        saved_county_names = state_counties.loc[
            state_counties["state_county_fips"].isin(current_codes), "county"
        ].tolist()

        col_label, col_remove = st.columns([6, 1])
        with col_label:
            st.subheader(state_name)
        with col_remove:
            if st.button("Remove", key=f"county_remove_{state_name}"):
                st.session_state.county_sel_states.remove(state_name)
                st.rerun()

        chosen = st.multiselect(
            f"Counties in {state_name}",
            options=county_options,
            default=saved_county_names,
            key=f"county_counties_{state_name}",
            label_visibility="collapsed",
        )

        selected_codes.extend(
            state_counties.loc[
                state_counties["county"].isin(chosen), "state_county_fips"
            ].tolist()
        )

    selected_codes.sort()
    st.caption(f"{len(selected_codes)} counties selected")

    if st.button("Save"):
        _save_fips_codes(project_dir, selected_codes)
        st.toast("County selection saved!", icon="✅")
