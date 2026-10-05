"""Marginals Groups editor tab.

Reads the marginals expressions CSV for the year being edited (from the
installed ``mazpop.configs`` package) and the project's
``configs/<year_key>_marginals_groups.yaml``, and provides a UI to build
custom aggregation bins per group.

The output YAML is consumed twice:

* ``mazpop.steps.get_acs_data`` via
  ``build_aggregated_variables_by_group``, which merges the census variables of
  every component in a bin into one downloaded column ``<group>_<bin>``;
* the Controls tab, which turns each bin into a control target.

A project has two runs (``base_year`` and ``history_year``), so every file and
session key in this module is namespaced by ``year_key``.
"""

import uuid
from collections import OrderedDict
from pathlib import Path

import streamlit as st
import yaml

from mazpop.gui import config_io

SESSION_PREFIXES = ("marg_",)


# ---------------------------------------------------------------------------
# session-state keys (all prefixed with 'marg_')
# ---------------------------------------------------------------------------


def _loaded_key(year_key: str) -> str:
    return f"marg_loaded__{year_key}"


def _selected_key(year_key: str) -> str:
    return f"marg_selected__{year_key}"


def _groups_widget_key(year_key: str) -> str:
    return f"marg_groups__{year_key}"


def _mode_key(year_key: str, group: str) -> str:
    return f"marg_mode__{year_key}__{group}"


def _bins_key(year_key: str, group: str) -> str:
    return f"marg_bins__{year_key}__{group}"


def _bin_widget_key(kind: str, year_key: str, group: str, bin_id: str) -> str:
    return f"marg_{kind}__{year_key}__{group}__{bin_id}"


def _add_bin_key(year_key: str, group: str) -> str:
    return f"marg_add_bin__{year_key}__{group}"


def _defaults(key: str, **kwargs) -> dict:
    """Return widget kwargs, but only when the key isn't already set.

    Streamlit treats a key present in session state as the widget's value and
    warns when a default is also supplied, so defaults are conditional.
    """
    return {} if key in st.session_state else kwargs


def get_bins(year_key: str, group: str) -> list[dict]:
    """Return the group's bins: ``[{'id', 'name', 'cols'}, ...]``.

    Bins carry a stable id so widget keys survive insertions and deletions
    (index-based keys would show the wrong bin's contents after a delete).
    """
    return st.session_state[_bins_key(year_key, group)]


def set_bins(year_key: str, group: str, bins: list[dict]):
    st.session_state[_bins_key(year_key, group)] = bins


def _new_bin(name: str, cols: list[str]) -> dict:
    return {"id": uuid.uuid4().hex[:8], "name": name, "cols": list(cols)}


def init_group_state(year_key: str, group: str, bins: list[dict] | None = None):
    mode_key = _mode_key(year_key, group)
    bins_key = _bins_key(year_key, group)
    if mode_key not in st.session_state:
        st.session_state[mode_key] = "aggregate" if bins else "passthrough"
    if bins_key not in st.session_state:
        st.session_state[bins_key] = bins or []


# ---------------------------------------------------------------------------
# YAML generation / parsing
# ---------------------------------------------------------------------------


class _FlowList(list):
    """Marker for PyYAML to render this list in flow (inline) style."""


class _FlowDict(OrderedDict):
    """Marker for PyYAML to render this dict in flow style."""


def _flow_list_representer(dumper, data):
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


def _flow_dict_representer(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items(), flow_style=True)


def _ordered_dict_representer(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


def _none_representer(dumper, _data):
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


def _setup_yaml():
    yaml.add_representer(_FlowList, _flow_list_representer)
    yaml.add_representer(_FlowDict, _flow_dict_representer)
    yaml.add_representer(OrderedDict, _ordered_dict_representer)
    yaml.add_representer(type(None), _none_representer)


_setup_yaml()


def build_yaml_dict(
    selected_groups: list[str],
    modes: dict[str, str],
    bins_by_group: dict[str, list[dict]],
    group_columns: "OrderedDict[str, list[str]]",
) -> OrderedDict:
    """Build the YAML structure for ``yaml.dump``.

    Passthrough groups are written one bin per column (``{col: [col]}``), which
    the pipeline merges back to the original per-column download.
    """
    out = OrderedDict()
    for group in selected_groups:
        if modes.get(group, "passthrough") == "passthrough":
            out[group] = [
                _FlowDict([(col, _FlowList([col]))]) for col in group_columns[group]
            ]
        else:
            out[group] = [
                _FlowDict([(b["name"], _FlowList(b["cols"]))])
                for b in bins_by_group.get(group, [])
            ]
    return out


def dump_yaml(data: OrderedDict) -> str:
    return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)


def parse_existing_yaml(
    yaml_text: str, group_columns: "OrderedDict[str, list[str]]"
) -> dict | None:
    """Parse a groups YAML into session-state-ready values.

    Returns ``{'selected_groups', 'modes', 'bins', 'stale_groups'}`` where
    ``bins`` is ``{group: [{'id', 'name', 'cols'}, ...]}``.
    """
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        return None

    selected: list[str] = []
    modes: dict[str, str] = {}
    bins: dict[str, list[dict]] = {}
    stale: list[str] = []

    for group_name, value in raw.items():
        group_name = str(group_name)
        if group_name not in group_columns:
            stale.append(group_name)
            continue

        selected.append(group_name)

        if value is None:
            modes[group_name] = "passthrough"
            continue

        group_bins: list[dict] = []
        for item in value:
            if isinstance(item, dict):
                for k, v in item.items():
                    cols = [str(s) for s in (v if isinstance(v, list) else [v])]
                    group_bins.append(_new_bin(str(k), cols))

        if all(len(b["cols"]) == 1 and b["cols"][0] == b["name"] for b in group_bins):
            modes[group_name] = "passthrough"
        else:
            modes[group_name] = "aggregate"
            bins[group_name] = group_bins

    return {
        "selected_groups": selected,
        "modes": modes,
        "bins": bins,
        "stale_groups": stale,
    }


# ---------------------------------------------------------------------------
# loading into session state
# ---------------------------------------------------------------------------


def _auto_load_yaml(yaml_path: Path, year_key: str, group_columns) -> list[str]:
    """Load an existing groups YAML into session state once per year.

    Returns the list of groups in the YAML that no longer exist in the config
    CSVs for this year.
    """
    if st.session_state.get(_loaded_key(year_key)):
        return st.session_state.get(f"marg_stale__{year_key}", [])

    stale: list[str] = []
    if yaml_path.exists():
        parsed = parse_existing_yaml(yaml_path.read_text(encoding="utf-8"), group_columns)
        if parsed:
            st.session_state[_selected_key(year_key)] = parsed["selected_groups"]
            stale = parsed["stale_groups"]
            for group in parsed["selected_groups"]:
                init_group_state(
                    year_key, group, bins=parsed["bins"].get(group)
                )
                st.session_state[_mode_key(year_key, group)] = parsed["modes"].get(
                    group, "passthrough"
                )

    st.session_state[f"marg_stale__{year_key}"] = stale
    st.session_state[_loaded_key(year_key)] = True
    return stale


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def render(project_dir: Path, year_key: str):
    """Render the Marginals Groups tab for one of the project's two runs."""
    try:
        acs_dir, dec_dir = config_io.get_config_dirs(project_dir, year_key)
    except Exception as exc:
        st.error(f"Could not load the package configs for this year: {exc}")
        return

    group_columns = config_io.binnable_group_columns(acs_dir, dec_dir)
    if not group_columns:
        st.error(f"No marginals groups found in {acs_dir} / {dec_dir}")
        return

    yaml_path = config_io.marginals_groups_path(project_dir, year_key)
    all_groups = list(group_columns)
    suffix_maps = {g: {c: c for c in cols} for g, cols in group_columns.items()}

    stale = _auto_load_yaml(yaml_path, year_key, group_columns)

    left_pane, main_pane = st.columns([1, 3])

    with left_pane:
        st.subheader("Marginals Groups")
        st.caption(f"Editing `{yaml_path.parent.name}/{yaml_path.name}`")
        selected = st.multiselect(
            "Groups to include",
            options=all_groups,
            default=st.session_state.get(_selected_key(year_key), []),
            key=_groups_widget_key(year_key),
        )
        st.session_state[_selected_key(year_key)] = selected

        if stale:
            st.warning(
                "These groups in the YAML are not available for this year and "
                "will be dropped when you save: " + ", ".join(f"`{g}`" for g in stale)
            )

        if not selected:
            st.info("Select one or more groups to begin.")

    with main_pane:
        for group in selected:
            _render_group(year_key, group, group_columns[group], suffix_maps[group])

        st.divider()
        _render_preview_and_save(
            project_dir, year_key, yaml_path, selected, group_columns, suffix_maps
        )


def _render_group(year_key: str, group: str, cols_in_group: list[str], smap: dict):
    suffixes = [smap[c] for c in cols_in_group]
    mode_key = _mode_key(year_key, group)
    radio_key = f"marg_mode_radio__{year_key}__{group}"

    with st.expander(
        f"**{group}** ({len(cols_in_group)} columns)", expanded=True
    ):
        st.caption("Available columns: " + ", ".join(f"`{s}`" for s in suffixes))

        # The widget keeps its own key; the mirrored state key is what the
        # preview/save reads (a widget key cannot be written after creation).
        index = {"passthrough": 0, "aggregate": 1}.get(
            st.session_state.get(mode_key, "passthrough"), 0
        )
        mode = st.radio(
            "Mode",
            ["passthrough", "aggregate"],
            key=radio_key,
            horizontal=True,
            help="passthrough downloads each column as its own control; "
            "aggregate merges the columns you assign into custom bins.",
            **_defaults(radio_key, index=index),
        )
        st.session_state[mode_key] = mode

        if mode == "aggregate":
            bins = get_bins(year_key, group)

            assigned: set[str] = set()
            for b in bins:
                assigned.update(b["cols"])
            unassigned = [s for s in suffixes if s not in assigned]
            if unassigned:
                st.warning(
                    "Unassigned columns are not controlled: "
                    + ", ".join(f"`{s}`" for s in unassigned)
                )

            kept: list[dict] = []
            changed = False
            deleted = False

            for b in bins:
                bid = b["id"]
                c1, c2, c3 = st.columns([2, 6, 1])

                name_key = _bin_widget_key("bin_name", year_key, group, bid)
                with c1:
                    new_name = st.text_input(
                        "Bin name",
                        key=name_key,
                        label_visibility="collapsed",
                        placeholder="Bin name",
                        **_defaults(name_key, value=b["name"]),
                    )

                cols_key = _bin_widget_key("bin_cols", year_key, group, bid)
                options = sorted(
                    set(b["cols"]) | set(unassigned),
                    key=lambda x: suffixes.index(x) if x in suffixes else 999,
                )
                with c2:
                    new_cols = st.multiselect(
                        "Columns",
                        options=options,
                        key=cols_key,
                        label_visibility="collapsed",
                        **_defaults(cols_key, default=b["cols"]),
                    )

                with c3:
                    if st.button("🗑️", key=_bin_widget_key("bin_del", year_key, group, bid)):
                        deleted = True
                        continue

                if new_name != b["name"] or list(new_cols) != list(b["cols"]):
                    b = {**b, "name": new_name, "cols": list(new_cols)}
                    changed = True
                kept.append(b)

            if changed or deleted:
                set_bins(year_key, group, kept)
                if deleted:
                    st.rerun()

            if st.button("➕ Add bin", key=_add_bin_key(year_key, group)):
                set_bins(year_key, group, get_bins(year_key, group) + [_new_bin("new_bin", [])])
                st.rerun()


def _collect_warnings(
    selected: list[str],
    group_columns,
    modes: dict[str, str],
    bins_by_group: dict[str, list[dict]],
) -> list[str]:
    warnings: list[str] = []
    for group in selected:
        if modes.get(group, "passthrough") != "aggregate":
            continue
        available = set(group_columns[group])
        names = [b["name"] for b in bins_by_group.get(group, [])]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            warnings.append(
                f"**{group}**: duplicate bin names — {', '.join(sorted(duplicates))}"
            )
        for b in bins_by_group.get(group, []):
            if not b["name"].strip():
                warnings.append(f"**{group}**: a bin has no name.")
            if not b["cols"]:
                warnings.append(f"**{group}**: bin '{b['name']}' has no columns assigned.")
            unknown = [c for c in b["cols"] if c not in available]
            if unknown:
                warnings.append(
                    f"**{group}**: bin '{b['name']}' uses columns that do not exist "
                    f"for this year — {', '.join(unknown)}"
                )
    return warnings


def _render_preview_and_save(
    project_dir: Path,
    year_key: str,
    yaml_path: Path,
    selected: list[str],
    group_columns,
    suffix_maps,
):
    st.subheader("YAML Output")

    modes = {g: st.session_state[_mode_key(year_key, g)] for g in selected}
    bins_by_group = {g: get_bins(year_key, g) for g in selected}

    yaml_dict = build_yaml_dict(selected, modes, bins_by_group, group_columns)
    yaml_text = dump_yaml(yaml_dict)

    for warning in _collect_warnings(selected, group_columns, modes, bins_by_group):
        st.warning(f"⚠️ {warning}")

    if not selected:
        st.info("No groups selected — saving writes an empty groups file.")
    st.code(yaml_text, language="yaml")

    if st.button("💾 Save", type="primary", key=f"marg_save__{year_key}"):
        try:
            yaml_path.parent.mkdir(parents=True, exist_ok=True)
            yaml_path.write_text(yaml_text, encoding="utf-8")
            st.success(f"Saved to {yaml_path.relative_to(Path(project_dir))}")
        except Exception as exc:  # noqa: BLE001 - surface any write failure
            st.error(f"Failed to save: {exc}")
