"""Minimal ``.env`` file helpers (no third-party dependency).

Secrets such as the Census API key are stored in a project-local, git-ignored
``.env`` file rather than OS-level environment variables.  Setting OS
environment variables from a GUI is unreliable across platforms and does not
work in cloud environments such as GitHub Codespaces (the value never reaches
the running process or new terminals).  A ``.env`` file that both the GUI
(``mazpop editor``) and the pipeline (``mazpop synthesize``) load behaves
identically everywhere.
"""

import os
from pathlib import Path

ENV_FILENAME = ".env"


def dotenv_path(base_dir) -> Path:
    """Return the path to the ``.env`` file for a project directory."""
    return Path(base_dir) / ENV_FILENAME


def read_dotenv(path) -> dict:
    """Parse a ``.env`` file into a ``{name: value}`` dict.

    Blank lines and lines starting with ``#`` are ignored.  Surrounding
    single or double quotes around values are stripped.  Returns an empty
    dict if the file does not exist.
    """
    path = Path(path)
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip("'\"")
        if name:
            result[name] = value
    return result


def write_dotenv_var(path, name: str, value: str) -> Path:
    """Set ``name=value`` in the ``.env`` file, preserving other entries.

    Replaces an existing assignment for ``name`` in place; appends it
    otherwise.  Creates the file if needed.
    """
    path = Path(path)
    existing = (
        path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    )
    new_line = f"{name}={value}"
    replaced = False
    out: list[str] = []
    for line in existing:
        stripped = line.strip()
        is_assignment = (
            stripped
            and not stripped.startswith("#")
            and stripped.split("=", 1)[0].strip() == name
        )
        if is_assignment:
            if not replaced:
                out.append(new_line)
                replaced = True
            # drop any duplicate assignments
        else:
            out.append(line)
    if not replaced:
        out.append(new_line)

    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def load_dotenv(path, override: bool = False) -> dict:
    """Load ``.env`` values into ``os.environ`` and return them.

    By default existing environment variables are left untouched; pass
    ``override=True`` to replace them.
    """
    values = read_dotenv(path)
    for name, value in values.items():
        if override or name not in os.environ:
            os.environ[name] = value
    return values
