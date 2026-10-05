"""Session-state housekeeping shared by the app shell and the Projects tab.

Each tab owns a set of session-state keys (widgets and cached configs). When
the active project changes, those keys must be dropped so the tabs re-read the
new project's configs instead of showing stale values from the previous one.
Keeping the prefixes here (rather than in the shell) avoids an import cycle,
since the Projects tab needs this helper but the shell imports the Projects
tab.
"""

import streamlit as st

from mazpop.gui import (
    census_key_editor,
    controls_editor,
    county_selector,
    marginals_editor,
)

# Session-state keys owned by the app shell itself.
ACTIVE_PROJECT_KEY = "active_project_dir"
YEAR_KEY = "year_key"
YEAR_WIDGET_KEY = "year_choice"
OWNED_KEYS = (ACTIVE_PROJECT_KEY, YEAR_KEY, YEAR_WIDGET_KEY)

# Prefixes owned by the tabs; every tab namespaces its keys with one of these.
OWNED_PREFIXES: tuple[str, ...] = (
    *census_key_editor.SESSION_PREFIXES,
    *county_selector.SESSION_PREFIXES,
    *marginals_editor.SESSION_PREFIXES,
    *controls_editor.SESSION_PREFIXES,
)


def clear_tab_session_state():
    """Remove session-state keys owned by the shell and the tabs.

    The active-project key is kept: callers set it before calling this.
    """
    for key in list(st.session_state.keys()):
        if key == ACTIVE_PROJECT_KEY:
            continue
        if key in OWNED_KEYS or key.startswith(OWNED_PREFIXES):
            del st.session_state[key]
