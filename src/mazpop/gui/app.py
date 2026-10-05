"""Streamlit app shell for mazpop.

``mazpop editor`` launches this via ``mazpop/gui/_launch.py``.  The shell
resolves the active project from session state (the Projects tab creates or
switches projects), picks the synthesis year being edited, and renders one tab
per editor.

Both the marginals-groups editor and the controls editor write inputs for a
single population synthesis run, and a project has two of them
(``history_year`` and ``base_year``), so the year selector lives in the
sidebar and applies to every tab.  Running the pipeline is intentionally not
part of the GUI; use ``mazpop synthesize -c <configs_dir>`` for that.

To add a new tab, import the module and add it to ``_TABS``.
"""

import os
from pathlib import Path

import streamlit as st

from mazpop.gui import (
    census_key_editor,
    config_io,
    county_selector,
    controls_editor,
    marginals_editor,
    project_creator,
)
from mazpop.gui.projects import list_projects, projects_root
from mazpop.gui.session_state import (
    ACTIVE_PROJECT_KEY,
    YEAR_KEY,
    YEAR_WIDGET_KEY,
    clear_tab_session_state,
)

# (label, render_function) — every render function takes (project_dir, year_key).
_TABS = [
    ("Census API Key", census_key_editor.render),
    ("County Selection", county_selector.render),
    ("Marginals Groups", marginals_editor.render),
    ("Controls", controls_editor.render),
]


def active_project_dir() -> Path | None:
    """Return the project directory selected in this session, if any."""
    value = st.session_state.get(ACTIVE_PROJECT_KEY)
    return Path(value) if value else None


def set_active_project_dir(path: Path):
    st.session_state[ACTIVE_PROJECT_KEY] = str(Path(path).resolve())


def _initial_project_dir() -> Path | None:
    """Resolve the project to open on first load.

    ``MAZPOP_EDITOR_PROJECT`` (set by ``mazpop editor --project``) wins; when
    exactly one project exists it is opened automatically.
    """
    requested = os.environ.get("MAZPOP_EDITOR_PROJECT")
    if requested:
        candidate = projects_root() / requested
        if candidate.is_dir():
            return candidate
        st.warning(f"Project '{requested}' was not found under {projects_root()}.")
        return None

    existing = list_projects()
    if len(existing) == 1:
        return projects_root() / existing[0]
    return None


def _render_year_selector(project_dir: Path) -> str:
    """Render the sidebar synthesis-year selector and return the year key."""
    try:
        options = config_io.year_options(project_dir)
    except Exception as exc:  # surface config errors without killing the GUI
        st.sidebar.error(f"Could not read settings.yaml: {exc}")
        return "base"

    if not options:
        st.sidebar.error("settings.yaml does not define base_year or history_year.")
        return "base"

    label_by_key = {
        year_key: config_io.year_label(project_dir, year_key)
        for year_key, _ in options
    }
    key_by_label = {label: year_key for year_key, label in label_by_key.items()}
    labels = list(key_by_label)

    current = st.session_state.get(YEAR_KEY, options[0][0])
    if current not in label_by_key:
        current = options[0][0]

    # Streamlit keeps the widget's own state; reset it when it holds a label
    # that no longer exists so the radio can always render.
    if st.session_state.get(YEAR_WIDGET_KEY) not in key_by_label:
        st.session_state[YEAR_WIDGET_KEY] = label_by_key[current]

    choice = st.sidebar.radio("Synthesis year", labels, key=YEAR_WIDGET_KEY)
    st.session_state[YEAR_KEY] = key_by_label[choice]

    year_key = st.session_state[YEAR_KEY]
    st.sidebar.caption(
        "Editing:\n\n"
        f"- `configs/{year_key}_marginals_groups.yaml`\n"
        f"- `popsim_{year_key}_year/configs/controls.csv`"
    )
    return year_key


def run(project_dir: Path | None = None):
    """Launch the GUI.

    Parameters
    ----------
    project_dir : Path, optional
        Project to open on first load. When omitted, the project requested via
        ``MAZPOP_EDITOR_PROJECT`` is opened, or the only existing project when
        there is exactly one; otherwise the user picks one in the Projects tab.
    """
    st.set_page_config(page_title="mazpop", layout="wide")

    if ACTIVE_PROJECT_KEY not in st.session_state:
        if project_dir is not None:
            set_active_project_dir(project_dir)
        else:
            initial = _initial_project_dir()
            if initial is not None:
                set_active_project_dir(initial)

    active = active_project_dir()
    st.title(f"🏘️ {active.name}" if active else "🏘️ mazpop")

    if active is None:
        st.info("Create a new project to get started.")
        project_creator.render(None)
        return

    year_key = _render_year_selector(active)

    tabs = st.tabs(["Projects"] + [label for label, _ in _TABS])
    with tabs[0]:
        project_creator.render(active)
    for tab, (_, render_fn) in zip(tabs[1:], _TABS):
        with tab:
            render_fn(active, year_key)
