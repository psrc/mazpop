"""Evaluate the project's calibration target expressions and export the targets.

Builds the history year block to base year tract crosswalk, loads the synthetic
housing unit and household tables from both PopulationSim runs, then evaluates
the expressions in the project's ``configs/calibration_data_expressions.csv``
in file order via :func:`mazpop.util.expressions.evaluate_expression_csv`.

Each expression row assigns one target (``expr_out``) from one pandas
expression (``expression``). The starting variables the expressions can
reference are the raw tables (``units_hist``, ``units_base``,
``households_hist``, ``households_base``), the block to tract crosswalk
(``block_tract_xwalk``) and the settings years (``history_year``,
``base_year``). Rows are evaluated in order, so an expression can use every
variable defined by an earlier row.

The resulting target tables are written to the project output directory.
"""

import pandas as pd

from mazpop.util.expressions import evaluate_expression_csv
from mazpop.util.pipeline import Pipeline
from mazpop.steps.create_geography_lookups import get_tract_tiger_year, download_tracts, download_blocks

EXPRESSIONS_FILE = 'calibration_data_expressions.csv'
UNITS_FILE = 'synthetic_housing_units.csv'
HOUSEHOLDS_FILE = 'synthetic_households.csv'

# variables that must exist after evaluating the expressions before export
EXPORTED_VARIABLES = ['housing_unit_targets', 'household_targets']

def create_block_tract_lookup(blocks_by_state, tracts_by_state):
    all_blocks = []
    for state_str, blocks in blocks_by_state.items():
        blocks = blocks.rename(columns={'block_id':'block_id_hist'}).copy()
        blocks.geometry = blocks.representative_point()
        all_blocks.append(blocks[['block_id_hist','geometry']])
    blocks_gdf = pd.concat(all_blocks, ignore_index=True)
    blocks_gdf = blocks_gdf.to_crs(epsg=4326)
    
    all_tracts = []
    for state_str, tracts in tracts_by_state.items():
        tracts = tracts.rename(columns={'tract_id':'tract_id_base'}).copy()
        all_tracts.append(tracts[['tract_id_base','geometry']])
    tracts_gdf = pd.concat(all_tracts, ignore_index=True)
    tracts_gdf = tracts_gdf.to_crs(epsg=4326)

    blocks_joined = blocks_gdf.sjoin(tracts_gdf, how='left')
    return blocks_joined[['block_id_hist','tract_id_base']]
        

def run_step(context):
    pipeline = Pipeline(context)
    today = pd.Timestamp.today().strftime('%Y-%m-%d')

    # spatial join history year blocks (2010) to base year tracts (2020)
    base_year = pipeline.base_year
    history_year = pipeline.history_year
    tract_tiger_year = get_tract_tiger_year(base_year)
    tracts_by_state = {
        state_str: download_tracts(tract_tiger_year, state_str, county_ids)
        for state_str, county_ids in pipeline.state_county_fips.items()
    }

    blocks_by_state = {
        state_str: download_blocks(history_year, state_str, county_ids)
        for state_str, county_ids in pipeline.state_county_fips.items()
    }

    block_tract_xwalk = create_block_tract_lookup(blocks_by_state, tracts_by_state)

    # load the synthetic tables from both populationSim runs
    popsim_output_dir_hist = pipeline.get_popsim_output_dir(history_year)
    popsim_output_dir_base = pipeline.get_popsim_output_dir(base_year)
    units_hist = pd.read_csv(popsim_output_dir_hist / UNITS_FILE)
    units_base = pd.read_csv(popsim_output_dir_base / UNITS_FILE)
    households_hist = pd.read_csv(popsim_output_dir_hist / HOUSEHOLDS_FILE)
    households_base = pd.read_csv(popsim_output_dir_base / HOUSEHOLDS_FILE)

    # evaluate calibration_data_expressions.csv in project_dir/configs
    namespace = evaluate_expression_csv(
        pipeline.settings_path / EXPRESSIONS_FILE,
        units_hist=units_hist,
        units_base=units_base,
        households_hist=households_hist,
        households_base=households_base,
        block_tract_xwalk=block_tract_xwalk,
        history_year=history_year,
        base_year=base_year,
    )
    missing = [name for name in EXPORTED_VARIABLES if name not in namespace]
    if missing:
        raise KeyError(
            f"{EXPRESSIONS_FILE} did not define {missing}. "
            "Add the missing rows or fix their expr_out names."
        )
    housing_unit_targets = namespace['housing_unit_targets']
    household_targets = namespace['household_targets']

    # export targets to project_dir/output
    housing_unit_targets.to_csv(
        f'{pipeline.output_dir}/housing_unit_calib_targets_{today}.csv', index=False
    )
    household_targets.to_csv(
        f'{pipeline.output_dir}/household_calib_targets_{today}.csv', index=False
    )
    print(
        f"Calibration targets written to {pipeline.output_dir}: "
        f"{len(housing_unit_targets):,} housing unit rows, "
        f"{len(household_targets):,} household rows."
    )

    return context