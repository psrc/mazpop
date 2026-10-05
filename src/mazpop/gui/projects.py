"""Location helpers for mazpop projects.

Deliberately free of streamlit imports (and of any pipeline imports) so it can
be used from the CLI, the app shell, and every tab.
"""

import os
from pathlib import Path

import yaml

TEMPLATE_NAME = "default_template"

# <repo>/src/mazpop/gui/projects.py -> <repo>
_REPO_ROOT = Path(__file__).resolve().parents[3]


def repo_root() -> Path:
    """Return the root of the mazpop checkout that contains this package."""
    return _REPO_ROOT


def projects_root() -> Path:
    """Return the directory that holds all projects, creating it if needed.

    Resolution order:

    1. ``$MAZPOP_PROJECTS_DIR`` when set,
    2. ``<current working directory>/projects`` when that directory exists,
    3. ``<repo>/projects``.
    """
    override = os.environ.get("MAZPOP_PROJECTS_DIR")
    if override:
        root = Path(override).expanduser().resolve()
    else:
        cwd_projects = Path.cwd() / "projects"
        root = cwd_projects.resolve() if cwd_projects.is_dir() else _REPO_ROOT / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def template_dir() -> Path:
    """Return the default project template shipped with the repo."""
    repo_template = _REPO_ROOT / "projects" / TEMPLATE_NAME
    if repo_template.is_dir():
        return repo_template
    return projects_root() / TEMPLATE_NAME


def list_projects() -> list[str]:
    """Return sorted names of existing project folders (excluding the template)."""
    return sorted(
        d.name
        for d in projects_root().iterdir()
        if d.is_dir() and d.name != TEMPLATE_NAME
    )


def find_project(name: str) -> Path | None:
    """Return the path of a project by name, or None when it does not exist."""
    if not name:
        return None
    candidate = projects_root() / name
    return candidate if candidate.is_dir() else None


def configs_dir(project_dir) -> Path:
    """Return the ``configs`` directory of a project."""
    return Path(project_dir) / "configs"


def settings_path(project_dir) -> Path:
    """Return the project's ``configs/settings.yaml`` path."""
    return configs_dir(project_dir) / "settings.yaml"


def load_settings(project_dir) -> dict:
    """Read a project's ``configs/settings.yaml``."""
    path = settings_path(project_dir)
    if not path.exists():
        raise FileNotFoundError(f"settings.yaml not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
