import pandas as pd
import us
from pathlib import Path

from mazpop.util.expressions import evaluate_expression_csv
from mazpop.util.pipeline import Pipeline


def get_lodes_jobs(pipeline,block_ids):
    industry_xwalk = pipeline.settings_path / 'industry_crosswalk.csv'
    industry_xwalk = pd.read_csv(industry_xwalk)

    lodes_version = pipeline.settings.get('lodes_version', 8)
    government_sector = pipeline.settings.get('government_sector', 98)
    
    jobs_out = pd.DataFrame()
    for state_str, _ in pipeline.state_county_fips.items():
        state_abbr = us.states.lookup(state_str).abbr.lower()
        all_jobs_url = f'https://lehd.ces.census.gov/data/lodes/LODES{lodes_version}/{state_abbr}/wac/{state_abbr}_wac_S000_JT00_{pipeline.base_year}.csv.gz'
        print(f"Downloading all jobs data from {all_jobs_url}")
        all_jobs = (
        pd.read_csv(all_jobs_url)
            .assign(
                block_id=lambda x: ('1' + x['w_geocode'].astype(str)).astype('int64')
                )
            .query('block_id in @block_ids')
            )
        priv_jobs_url = f'https://lehd.ces.census.gov/data/lodes/LODES{lodes_version}/{state_abbr}/wac/{state_abbr}_wac_S000_JT02_{pipeline.base_year}.csv.gz'
        print(f"Downloading private jobs data from {priv_jobs_url}")
        priv_jobs = (
                pd.read_csv(priv_jobs_url)
                .assign(
                block_id=lambda x: ('1' + x['w_geocode'].astype(str)).astype('int64')
            )
            .query('block_id in @block_ids')
        )
        cns_to_industry = industry_xwalk.set_index('cns')['industry'].to_dict()

        priv_jobs = priv_jobs.set_index('block_id')

        df = priv_jobs[[c for c in priv_jobs.columns if c.startswith('CNS')]]

        df_long = (df
                .melt(ignore_index=False, var_name='cns', value_name='jobs')
                .assign(industry=lambda x: x['cns'].map(cns_to_industry))
                .reset_index())

        gov_jobs = (
            all_jobs.set_index('block_id')['C000']
            .subtract(priv_jobs.reset_index().set_index('block_id')['C000'], fill_value=0)
            .astype(int)
        )

        gov_cns = next(c for c, i in cns_to_industry.items() if i == government_sector)

        df_long['jobs'] = df_long['jobs'].where(
            df_long['industry'] != government_sector,
            df_long['jobs'] + df_long['block_id'].map(gov_jobs).fillna(0)
        ).astype(int)

        # blocks with government-only jobs are absent from the private (JT02) file
        missing = all_jobs.loc[~all_jobs['block_id'].isin(df_long['block_id']), ['block_id', 'C000']]
        df_long = pd.concat(
            [df_long, missing.rename(columns={'C000': 'jobs'}).assign(cns=gov_cns, industry=government_sector)],
            ignore_index=True
        )
        df_long = df_long.rename(columns={'industry':'sector_id'})
        # create a new dataframe with one row per job
        j = df_long.loc[df_long.index.repeat(df_long['jobs'])].copy()
        j['job_id'] = j.reset_index().index + 1
        j = j[['job_id','sector_id','block_id']].copy()
        jobs_out = pd.concat([jobs_out, j], ignore_index=True)
    return jobs_out

def run_step(context):
    pipeline = Pipeline(context)
    blocks = pipeline.get_table(f'blocks_{pipeline.base_year}')
    block_ids = blocks['block_id'].tolist()

    # download and process LODES jobs data
    jobs = get_lodes_jobs(pipeline, block_ids)
    jobs['maz_id'] = jobs['block_id'].map(blocks.set_index('block_id')['maz_id'])

    # save the processed jobs data to the output directory
    today = pd.Timestamp.today().strftime('%Y-%m-%d')
    out_path = Path(pipeline.output_dir) / f'jobs_{today}.csv'
    print(f"Saving processed jobs table to {out_path}")
    jobs.drop(columns=['block_id']).to_csv(out_path, index=False)