"""Split PopulationSim's synthetic households into household and unit tables.

blockpop synthesizes vacant units alongside occupied ones, so PopulationSim's
``synthetic_households.csv`` is really a housing-unit table. This step writes:

* ``synthetic_households_final.csv`` — occupied units only, without the
  vacancy columns.
* ``synthetic_housing_units.csv`` — every unit keyed by ``unit_id`` with an
  owner/renter x single/multi-family ``unit_type_id``.
* ``housing_unit_types.csv`` — the ``unit_type_id`` lookup.

Runs automatically at the end of ``blockpop.util.popsim_main``. It can also be
re-run on its own against an existing output directory::

    uv run python -m blockpop.util.housing_units -o output
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

SYNTHETIC_HOUSEHOLDS = 'popsim_hh.csv'
HOUSEHOLDS_FILE = 'synthetic_households.csv'
HOUSING_UNITS_FILE = 'synthetic_housing_units.csv'
UNIT_TYPES_FILE = 'housing_unit_types.csv'

# building_type is the PUMS BLD code: 1 mobile home, 2 single-family detached,
# 3 single-family attached, 4 two units, 5 three-four units, 6-9 five or more
# units, 10 boat/RV/van.
SF_BUILDING_TYPES = (1, 2, 3, 4, 10)
MF_BUILDING_TYPES = (5, 6, 7, 8, 9)

# vacant_type is the ACS vacancy status: 1 for rent, 2 rented not occupied,
# 3 for sale only, 4 sold not occupied, 5 seasonal, 6 migrant workers, 7 other.
OWNER_VACANT_TYPES = (3, 4, 5)
RENTER_VACANT_TYPES = (1, 2, 6, 7)

UNIT_TYPES = {
    1: 'Single-family (1-2 units) owner-occupied',
    2: 'Multi-family (3+ units) owner-occupied',
    3: 'Single-family (1-2 units) renter-occupied',
    4: 'Multi-family (3+ units) renter-occupied',
}


def assign_unit_types(units):
    """Add ``unit_id`` and ``unit_type_id`` columns to the units table."""
    sf_mask = units['building_type'].isin(SF_BUILDING_TYPES)
    mf_mask = units['building_type'].isin(MF_BUILDING_TYPES)
    owner_mask = (units['tenure'] == 1) | units['vacant_type'].isin(OWNER_VACANT_TYPES)
    renter_mask = (units['tenure'] == 2) | units['vacant_type'].isin(RENTER_VACANT_TYPES)

    units.loc[sf_mask & owner_mask, 'unit_type_id'] = 1
    units.loc[mf_mask & owner_mask, 'unit_type_id'] = 2
    units.loc[sf_mask & renter_mask, 'unit_type_id'] = 3
    units.loc[mf_mask & renter_mask, 'unit_type_id'] = 4

    units['unit_id'] = range(1, len(units) + 1)
    return units


def run(output_dir):
    """Write the household, housing-unit, and unit-type tables."""
    output_dir = Path(output_dir)
    source = output_dir / SYNTHETIC_HOUSEHOLDS
    if not source.exists():
        raise FileNotFoundError(f'PopulationSim output not found: {source}')

    print(f'Building housing unit tables from {source}')
    units = pd.read_csv(source)

    households = units.loc[units['is_vacant'] == 0].drop(
        columns=['is_vacant', 'vacant_type']
    )
    households.to_csv(output_dir / HOUSEHOLDS_FILE, index=False)

    units = assign_unit_types(units)
    units[['unit_id', 'block_id', 'year_built', 'unit_type_id']].to_csv(
        output_dir / HOUSING_UNITS_FILE, index=False
    )

    unit_types = pd.DataFrame(
        list(UNIT_TYPES.items()), columns=['unit_type_id', 'description']
    )
    unit_types.to_csv(output_dir / UNIT_TYPES_FILE, index=False)

    print(
        f'Wrote {len(households):,} households and {len(units):,} housing units to '
        f'{output_dir}'
    )
    return output_dir


def add_run_args(parser):
    parser.add_argument(
        '-o',
        '--output',
        type=str,
        metavar='PATH',
        default='output',
        help='path to the PopulationSim output dir (default: output)',
    )


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    add_run_args(parser)
    args = parser.parse_args()
    run(args.output)
    sys.exit(0)
