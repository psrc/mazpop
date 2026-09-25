import argparse
import sys
from pathlib import Path

from pypyr import pipelinerunner

from mazpop import __doc__, __version__
from mazpop.cli import CLI


def add_run_args(parser):
    parser.add_argument(
        "-c",
        "--configs_dir",
        type=str,
        metavar="PATH",
        default="configs",
        help="path to configs dir that contains settings.yaml (default: configs)",
    )


def run(args):
    configs_dir = str(Path(args.configs_dir).resolve())
    print(f"Running mazpop pipeline with configs in: {configs_dir}")
    pipelinerunner.run(f"{configs_dir}/settings", dict_in={"configs_dir": configs_dir})


def main():
    run_model = CLI(version=__version__, description=__doc__)
    add_run_args(run_model.parser)
    run_model.parser.set_defaults(func=run)

    sys.exit(run_model.execute())


if __name__ == "__main__":
    main()