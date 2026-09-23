import os
import pandas as pd

from mazpop.util.pipeline import Pipeline


def create_puma_block_lookup(pipeline):
    year = pipeline.context['year']
    blocks = pipeline.get_table(f'dec_data_{year}/dec_totals')[['geoid']]
    blocks = blocks.rename(columns={'geoid': 'block_id'})
    blocks['tract_id'] = blocks['block_id'].astype(str).str[:12].astype('int64')
    blocks['county_id'] = blocks['block_id'].astype(str).str[:6].astype('int64')
    blocks['block_id'] = blocks['block_id'].astype('int64')

    popsim_data_dir = pipeline.get_popsim_root_dir() / 'data'
    puma_tract_lookup = pd.read_csv(
        os.path.join(popsim_data_dir, 'puma_tract_lookup.csv')
    )

    result = blocks.merge(puma_tract_lookup, on='tract_id', how='left')
    result = result[['block_id', 'tract_id', 'county_id', 'puma_id', 'region']]
    result.to_csv(
        os.path.join(popsim_data_dir, 'puma_block_lookup.csv'), index=False
    )


def run_step(context):
    print("Creating PUMA <-> block lookup table...")
    pipeline = Pipeline(context)
    create_puma_block_lookup(pipeline)
    return context
