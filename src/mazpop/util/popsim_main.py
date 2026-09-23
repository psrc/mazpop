"""PopulationSim entry point patched for zero-person (vacant) households.

blockpop synthesizes vacant housing units as households with no person records.
Two places in PopulationSim assume every household has at least one person:

* ``setup_data_structures.build_incidence_table`` aggregates person-level
  control incidence with a groupby on the persons table and assigns the result
  to a household-indexed frame. Households absent from the groupby become NaN,
  and with ``GROUP_BY_INCIDENCE_SIGNATURE`` those rows are silently dropped
  from the grouped incidence table, removing every vacant unit from the
  synthesis.
* ``write_synthetic_population.merge_seed_data`` left-joins the persons table
  onto the expanded households, which emits one all-null row per vacant unit in
  the synthetic persons output.

Both fixes are upstreamed in the blockpop populationsim fork; this module keeps
blockpop working against the released package. Invoked as
``python -m blockpop.util.popsim_main`` in place of ``python -m populationsim``.

On a successful run it also builds the blockpop housing-unit tables
(see ``blockpop.util.housing_units``).
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from populationsim.core import config
from populationsim.steps import setup_data_structures as _setup_data_structures
from populationsim.steps import write_synthetic_population as _write_synthetic_population

from mazpop.util import housing_units

_build_incidence_table = _setup_data_structures.build_incidence_table
_merge_seed_data = _write_synthetic_population.merge_seed_data


def _build_incidence_table_zero_filled(control_spec, households_df, persons_df, crosswalk_df):
    table = _build_incidence_table(control_spec, households_df, persons_df, crosswalk_df)
    return table.fillna(0).astype(np.int64)


def _merge_seed_data_inner(expanded_household_ids, seed_data_df, seed_columns, trace_label):
    hh_col = config.setting("household_id_col")
    # the persons table joins on a column; households joins on its index
    if seed_data_df.index.name != hh_col and hh_col in seed_data_df.columns:
        expanded_household_ids = expanded_household_ids[
            expanded_household_ids[hh_col].isin(seed_data_df[hh_col])
        ]
    return _merge_seed_data(
        expanded_household_ids, seed_data_df, seed_columns, trace_label
    )


_setup_data_structures.build_incidence_table = _build_incidence_table_zero_filled
_write_synthetic_population.merge_seed_data = _merge_seed_data_inner


def _output_dir():
    """Resolve the output dir from the same args PopulationSim was given."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('-w', '--working_dir')
    parser.add_argument('-o', '--output')
    args, _ = parser.parse_known_args()
    if args.output:
        return Path(args.output)
    return Path(args.working_dir or Path.cwd()) / 'output'


if __name__ == '__main__':
    from populationsim.__main__ import main

    # populationsim's main() always exits; only post-process a clean run
    try:
        main()
    except SystemExit as exit_status:
        if exit_status.code not in (0, None):
            raise

    housing_units.run(_output_dir())
    sys.exit(0)
