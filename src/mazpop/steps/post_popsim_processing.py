"""Evaluate the project's post-PopulationSim expressions and export final tables.

Loads PopulationSim's output (``popsim_hh.csv`` housing units and
``synthetic_persons.csv``) plus the ``blocks_<year>`` table, then evaluates the
expressions in the project's ``configs/post_popsim_expressions.csv`` in file
order via :func:`mazpop.util.expressions.evaluate_expression_csv`.

Each expression row assigns one target (``expr_out``) from one pandas
expression (``expression``). Targets can be a table (``households``), a new
column (``units['unit_id']``), a masked assignment
(``units.loc[sf_mask, 'unit_type_id']``), or a helper value (masks, dicts).
Rows are evaluated in order, so an expression can use every variable defined
by an earlier row.

The resulting tables are written to the project output directory.
"""

from pathlib import Path

import pandas as pd

from mazpop.util.expressions import evaluate_expression_csv
from mazpop.util.pipeline import Pipeline

EXPRESSIONS_FILE = 'post_popsim_expressions.csv'
UNITS_FILE = 'popsim_hh.csv'
PERSONS_FILE = 'synthetic_persons.csv'

# variables that must exist after evaluating the expressions before export
EXPORTED_VARIABLES = ['households', 'units', 'blocks', 'persons', 'unit_types_df']


def run_step(context):
    pipeline = Pipeline(context)
    year = pipeline.context['year']
    popsim_output_dir = Path(pipeline.get_popsim_root_dir()) / 'output'
    project_output_dir = Path(pipeline.get_output_dir())
    today = pd.Timestamp.today().strftime('%Y-%m-%d')

    # load tables
    blocks = pipeline.get_table(f'blocks_{year}')
    duplicated_blocks = int(blocks['block_id'].duplicated().sum())
    if duplicated_blocks:
        print(
            f"Warning: blocks_{year} has {duplicated_blocks:,} duplicate block_id "
            "rows (check the create_geography_lookups output). Keeping the first "
            "maz_id per block so the block_id -> maz_id lookup is unique."
        )
    maz_ids = blocks.drop_duplicates('block_id').set_index('block_id')['maz_id']
    units = pd.read_csv(popsim_output_dir / UNITS_FILE)
    persons = pd.read_csv(popsim_output_dir / PERSONS_FILE)
    loaded_units, loaded_persons = len(units), len(persons)

    # evaluate post_popsim_expressions.csv in project_dir/configs
    namespace = evaluate_expression_csv(
        pipeline.settings_path / EXPRESSIONS_FILE,
        units=units, persons=persons, blocks=blocks, maz_ids=maz_ids
    )
    missing = [name for name in EXPORTED_VARIABLES if name not in namespace]
    if missing:
        raise KeyError(
            f"{EXPRESSIONS_FILE} did not define {missing}. "
            "Add the missing rows or fix their expr_out names."
        )
    households = namespace['households']
    units = namespace['units']
    blocks = namespace['blocks']
    persons = namespace['persons']
    unit_types_df = namespace['unit_types_df']

    dropped_units = loaded_units - len(units)
    dropped_persons = loaded_persons - len(persons)
    if dropped_units or dropped_persons:
        print(
            f"Dropped {dropped_units:,} of {loaded_units:,} units and "
            f"{dropped_persons:,} of {loaded_persons:,} persons that fall "
            "outside the clipped block area."
        )

    # export tables to project_dir/output
    households.to_csv(project_output_dir / f'synthetic_households_{today}.csv', index=False)
    units.to_csv(project_output_dir / f'synthetic_housing_units_{today}.csv', index=False)
    blocks.to_csv(project_output_dir / f'blocks_{year}_{today}.csv', index=False)
    persons.to_csv(project_output_dir / f'synthetic_persons_{today}.csv', index=False)
    unit_types_df.to_csv(project_output_dir / 'housing_unit_types.csv', index=False)
    print(
        f"Post-popsim tables written to {project_output_dir}: "
        f"{len(households):,} households, {len(units):,} housing units, "
        f"{len(persons):,} persons, {len(blocks):,} blocks."
    )

    return context
