import os
import subprocess
import sys
from importlib import resources
from pathlib import Path

from pypyr import pipelinerunner

from mazpop import __doc__, __version__
from mazpop.cli import CLI


def add_synthesize_args(parser):
    parser.add_argument(
        "-c",
        "--configs_dir",
        type=str,
        metavar="PATH",
        default="configs",
        help="path to configs dir that contains settings.yaml (default: configs)",
    )


def synthesize(args):
    configs_dir = str(Path(args.configs_dir).resolve())
    print(f"Running mazpop pipeline with configs in: {configs_dir}")
    pipelinerunner.run(f"{configs_dir}/settings", dict_in={"configs_dir": configs_dir})


def add_editor_args(parser):
    parser.add_argument(
        "-p",
        "--project",
        type=str,
        metavar="NAME",
        default=None,
        help="name of the project under the projects dir to open on first load",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="port for the editor web server (default: streamlit's default port)",
    )


def _launcher_path() -> Path:
    """Return the on-disk path of the streamlit entry point script.

    Streamlit requires a script file (not a module), so the GUI ships
    ``mazpop/gui/_launch.py`` and this resolves it inside the installed
    package without importing streamlit.
    """
    return Path(str(resources.files("mazpop.gui") / "_launch.py"))


def editor(args):
    cmd = [sys.executable, "-m", "streamlit", "run", str(_launcher_path())]
    if args.port is not None:
        cmd += ["--server.port", str(args.port)]

    env = dict(os.environ)
    if args.project:
        env["MAZPOP_EDITOR_PROJECT"] = args.project

    print("Launching the mazpop editor GUI...")
    print(f"Command: {' '.join(cmd)}")
    sys.exit(subprocess.call(cmd, env=env))


def main():
    run_model = CLI(version=__version__, description=__doc__)
    run_model.add_subcommand(
        "synthesize",
        add_synthesize_args,
        synthesize,
        "Run the population synthesis pipeline for a project",
    )
    run_model.add_subcommand(
        "editor",
        add_editor_args,
        editor,
        "Launch the interactive editor GUI for project marginals and controls",
    )
    sys.exit(run_model.execute())


if __name__ == "__main__":
    main()
