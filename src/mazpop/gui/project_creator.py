"""Projects tab for the GUI.

Lets the user name a new project and copies the default template into a fresh
folder under the projects directory, or switch to an existing project.  After
creation the active session is switched to the new project so subsequent tab
edits target the correct configs.

``project_dir`` is ``None`` when no project is active yet.
"""

import re
import shutil
from pathlib import Path

import streamlit as st

from mazpop.gui import config_io
from mazpop.gui.projects import (
    list_projects,
    load_settings,
    projects_root,
    template_dir,
)
from mazpop.gui.session_state import ACTIVE_PROJECT_KEY, clear_tab_session_state

# Characters allowed in project names (filesystem-safe)
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")


def _switch_project(dest: Path):
    """Make ``dest`` the active project and drop the previous project's tabs state."""
    st.session_state[ACTIVE_PROJECT_KEY] = str(dest.resolve())
    clear_tab_session_state()


def _render_summary(project_dir: Path):
    """Show the settings that drive the two synthesis runs."""
    try:
        options = config_io.year_options(project_dir)
        settings = load_settings(project_dir)
    except Exception as exc:
        st.error(f"Could not read configs/settings.yaml: {exc}")
        return

    rows = []
    for year_key, year in options:
        rows.append(
            {
                "run": year_key,
                "year": year,
                "acs_year": settings.get(f"{year_key}_acs_year", ""),
                "configs": f"popsim_{year_key}_year/configs",
            }
        )
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.warning(
            "settings.yaml defines neither `base_year` nor `history_year`; "
            "add them before configuring marginals."
        )


def render(project_dir: Path | None):
    st.header("Projects")

    if project_dir is not None:
        try:
            shown = project_dir.relative_to(projects_root())
        except ValueError:
            shown = project_dir
        st.caption(f"Active project: `{shown}`")
        _render_summary(project_dir)

    st.divider()

    # --- create ---
    st.subheader("Create new project")
    name = st.text_input(
        "New project name",
        help="Letters, digits, hyphens, and underscores only.",
    )

    if st.button("Create", type="primary"):
        if not name:
            st.error("Please enter a project name.")
            return
        if not _NAME_RE.match(name):
            st.error(
                "Name must start with a letter or digit and contain only "
                "letters, digits, hyphens, or underscores."
            )
            return

        dest = projects_root() / name
        if dest.exists():
            st.error(f"A project named **{name}** already exists.")
            return

        template = template_dir()
        if not template.exists():
            st.error(f"Default template folder not found: {template}")
            return

        shutil.copytree(template, dest)
        _switch_project(dest)
        st.rerun()

    st.divider()

    # --- existing projects ---
    available = list_projects()
    if available:
        st.markdown(
            "**Existing projects:** " + ", ".join(f"`{p}`" for p in available)
        )

        st.subheader("Open project")
        active_name = project_dir.name if project_dir else None
        selected = st.selectbox(
            "Existing project",
            options=available,
            index=available.index(active_name) if active_name in available else 0,
            key="switch_project_select",
        )
        if st.button("Open", disabled=selected == active_name):
            _switch_project(projects_root() / selected)
            st.rerun()
    else:
        st.info("No projects created yet.")
