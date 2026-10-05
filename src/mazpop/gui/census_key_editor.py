"""Census API Key tab for the project GUI.

Lets the user supply a Census API key in one of two ways:

1. **Paste a key** — the key is saved to the project's git-ignored ``.env``
   file under an environment-variable name (default ``CENSUS_KEY``, editable)
   and that name is recorded in the project's ``configs/settings.yaml`` under
   ``census_key``.
2. **Use an existing env var** — the user simply records the name of an
   environment variable they have already created.

In both cases ``settings.yaml`` stores only the *name* of the environment
variable, never the key itself.  The pipeline resolves the actual key at
runtime: ``Pipeline.get_census_key`` loads the project ``.env`` first and then
reads ``os.getenv(name)``, so a key saved here is picked up by
``mazpop synthesize`` without exporting anything in the shell.
"""

import os
import re
from pathlib import Path

import streamlit as st

from mazpop.gui.projects import settings_path
from mazpop.util.env import dotenv_path, load_dotenv, read_dotenv, write_dotenv_var

SESSION_PREFIXES = ("census_key_",)

# Census API key signup page
_SIGNUP_URL = "https://api.census.gov/data/key_signup.html"

# Default environment variable name when the user pastes a raw key
_DEFAULT_VAR_NAME = "CENSUS_KEY"

# Valid environment variable name (POSIX-portable identifier)
_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ---------------------------------------------------------------------------
# settings file helpers
# ---------------------------------------------------------------------------


def _read_settings_census_key(project_dir: Path) -> str | None:
    """Return the current ``census_key`` value from settings.yaml, if any.

    The line is parsed by hand (rather than with a YAML loader) so comments and
    formatting elsewhere in the file are irrelevant; a trailing inline comment
    and/or surrounding quotes are stripped from the value.
    """
    path = settings_path(project_dir)
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        value = _parse_census_key_line(line)
        if value is not None:
            return value
    return None


def _parse_census_key_line(line: str) -> str | None:
    """Return the value of a ``census_key:`` line, or None if it isn't one."""
    match = re.match(r"^\s*census_key:\s*(?P<value>.*?)\s*$", line)
    if not match:
        return None
    value = match.group("value")
    if value[:1] in ("'", '"'):
        # A quoted value may itself contain ' #', so honour the quotes first.
        end = value.find(value[0], 1)
        if end != -1:
            return value[1:end] or None
    value = re.split(r"\s+#", value, maxsplit=1)[0].strip().strip("'\"")
    return value or None


def _settings_comment(project_dir: Path) -> str:
    """Return any trailing comment on the existing ``census_key`` line."""
    path = settings_path(project_dir)
    if not path.exists():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*census_key:\s*.*?(\s+#.*)$", line)
        if match:
            return match.group(1)
    return ""


def _write_settings_census_key(project_dir: Path, var_name: str):
    """Write ``census_key: <var_name>`` to settings.yaml.

    Replaces an existing ``census_key:`` line in place, keeping its trailing
    comment, to preserve the rest of the file (other settings and the ``steps:``
    list); appends the key if it is not present.
    """
    path = settings_path(project_dir)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()

    replaced = False
    for i, line in enumerate(lines):
        if re.match(r"^\s*census_key:\s*", line):
            comment = _settings_comment(project_dir)
            lines[i] = f"census_key: {var_name}{comment}"
            replaced = True
            break

    if not replaced:
        lines.append(f"census_key: {var_name}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Key persistence (.env file)
# ---------------------------------------------------------------------------


def _save_key(project_dir: Path, name: str, value: str) -> Path:
    """Persist the key to the project's ``.env`` file and current process.

    Writing to a git-ignored ``.env`` file (rather than an OS-level environment
    variable) works reliably across local machines and cloud environments such
    as GitHub Codespaces, where variables set from a GUI do not propagate to
    the running process or to new terminals.
    """
    env_path = write_dotenv_var(dotenv_path(project_dir), name, value)
    # Make it available to the running process immediately
    os.environ[name] = value
    return env_path


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def render(project_dir: Path, year_key: str):
    st.header("Census API Key")
    st.caption("Applies to both the base year and history year runs.")

    # Load any key saved to the project's .env so the status below can see it
    # after a restart.
    load_dotenv(dotenv_path(project_dir))

    # st.rerun() discards anything rendered before it, so success messages must
    # survive via session_state rather than being shown immediately.
    flash = st.session_state.pop("census_key_flash", None)
    if flash:
        st.success(flash)

    st.markdown(
        "A Census API key is required to download ACS and Decennial data. "
        f"Don't have one yet? [Sign up for a free key]({_SIGNUP_URL})."
    )

    # --- current status ------------------------------------------------------
    current_name = _read_settings_census_key(project_dir)
    if current_name:
        env_values = read_dotenv(dotenv_path(project_dir))
        is_set = bool(os.environ.get(current_name) or env_values.get(current_name))
        status = "✅ set" if is_set else "⚠️ not found in this environment"
        st.caption(f"Current setting — `census_key: {current_name}` ({status})")
    else:
        st.caption("No `census_key` is configured for this project yet.")

    st.divider()

    mode = st.radio(
        "How would you like to provide your key?",
        options=[
            "Paste my Census API key",
            "Use an existing environment variable",
        ],
        key="census_key_mode",
    )

    # --- mode 1: paste a key -------------------------------------------------
    if mode == "Paste my Census API key":
        key_value = st.text_input(
            "Census API key",
            type="password",
            help="Stored in the project's git-ignored .env file; "
            "it is not written to settings.yaml.",
        )
        var_name = st.text_input(
            "Environment variable name",
            value=current_name or _DEFAULT_VAR_NAME,
            help="The key will be stored under this name in the .env file.",
        )

        if st.button("Save key", type="primary"):
            if not key_value.strip():
                st.error("Please paste your Census API key.")
                return
            if not _VAR_NAME_RE.match(var_name.strip()):
                st.error(
                    "Variable name must start with a letter or underscore and "
                    "contain only letters, digits, and underscores."
                )
                return

            var_name = var_name.strip()
            try:
                env_path = _save_key(project_dir, var_name, key_value.strip())
            except Exception as exc:  # noqa: BLE001 - surface any write failure
                st.error(f"Failed to save key: {exc}")
                return

            _write_settings_census_key(project_dir, var_name)
            st.session_state["census_key_flash"] = (
                f"Saved key to `{var_name}` in `{env_path}` and recorded "
                f"`census_key: {var_name}` in settings.yaml."
            )
            st.rerun()

    # --- mode 2: existing env var --------------------------------------------
    else:
        var_name = st.text_input(
            "Existing environment variable name",
            value=current_name or _DEFAULT_VAR_NAME,
            help="The name of an environment variable you've already set to "
            "your Census API key.",
        )

        if var_name.strip():
            if os.environ.get(var_name.strip()):
                st.info(f"`{var_name.strip()}` is set in this environment.")
            else:
                st.warning(
                    f"`{var_name.strip()}` is not set in this environment. "
                    "Make sure to create it before running the pipeline."
                )

        if st.button("Save name", type="primary"):
            if not _VAR_NAME_RE.match(var_name.strip()):
                st.error(
                    "Variable name must start with a letter or underscore and "
                    "contain only letters, digits, and underscores."
                )
                return

            _write_settings_census_key(project_dir, var_name.strip())
            st.session_state["census_key_flash"] = (
                f"Recorded `census_key: {var_name.strip()}` in settings.yaml."
            )
            st.rerun()
