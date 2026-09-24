"""Run PopulationSim using the marginals produced by create_marginals.

Runs PopulationSim using the
popsim ``configs/`` directory (settings.yaml + controls.csv), the popsim
``data`` directory (seed tables, crosswalk, and marginals), and the popsim
``output`` directory.  Nothing is copied or staged into a working subfolder;
PopulationSim is pointed at these directories directly via its
``-c``/``-d``/``-o`` arguments.
"""

import subprocess
from pathlib import Path

from mazpop.util.pipeline import Pipeline


# Files that must already exist in the project data dir for popsim to run.
# These filenames must match the ``input_table_list`` in popsim settings.yaml.
_REQUIRED_DATA_FILES = [
    "seed_households.csv",
    "seed_persons.csv",
    "puma_block_lookup.csv",
    "tract_marginals.csv",
    "block_marginals.csv",
]


def prepare_run(pipeline: Pipeline) -> tuple[Path, Path, Path]:
    """Validate inputs and return ``(config_dir, data_dir, output_dir)``.

    Runs against the project's existing folders without copying anything.
    Raises ``FileNotFoundError`` if a required config or data file is missing.
    """
    popsim_root_dir = pipeline.get_popsim_root_dir()
    config_dir = popsim_root_dir / "configs"
    data_dir = popsim_root_dir / "data"
    output_dir = popsim_root_dir / "output"

    settings_file = config_dir / "settings.yaml"
    if not settings_file.exists():
        raise FileNotFoundError(
            f"settings.yaml not found: {settings_file} "
            "(run the create_seed_data step to generate it)"
        )
    controls_file = config_dir / "controls.csv"
    if not controls_file.exists():
        raise FileNotFoundError(
            f"controls.csv not found: {controls_file} "
            "(create it in the Controls tab)"
        )
    for name in _REQUIRED_DATA_FILES:
        src = data_dir / name
        if not src.exists():
            raise FileNotFoundError(
                f"Required popsim input not found: {src} "
                "(run the marginals pipeline first)"
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    return config_dir, data_dir, output_dir


def get_command(
    pipeline: Pipeline,
    config_dir: Path,
    data_dir: Path,
    output_dir: Path,
) -> list[str]:
    """Return the PopulationSim launch command (overridable in settings).

    Points PopulationSim at the given config, data, and output directories.
    """
    cmd = pipeline.settings.get("populationsim_command")
    if cmd:
        base = cmd.split() if isinstance(cmd, str) else list(cmd)
    else:
        base = ["uv", "run", "python", "-m", "mazpop.util.popsim_main"]
    return base + [
        "-c", str(config_dir),
        "-d", str(data_dir),
        "-o", str(output_dir),
    ]


def run_step(context):
    """Run PopulationSim in place. Returns the output directory path."""
    pipeline = Pipeline(context)
    config_dir, data_dir, output_dir = prepare_run(pipeline)
    command = get_command(pipeline, config_dir, data_dir, output_dir)
    print(f"Running PopulationSim (config={config_dir}, data={data_dir}, output={output_dir})")
    print(f"Command: {' '.join(command)}")

    result = subprocess.run(command, cwd=str(pipeline.root_dir), check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"PopulationSim exited with code {result.returncode}. "
            f"See logs in {output_dir}."
        )
    print(f"PopulationSim complete. Outputs in: {output_dir}")
    return context
