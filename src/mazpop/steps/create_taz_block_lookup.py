import geopandas as gpd
from pathlib import Path
import pandas as pd
from mazpop.util.pipeline import Pipeline


def load_taz_layer(pipeline):
    taz_path = Path.joinpath(pipeline.get_data_dir(), pipeline.settings['taz_shapefile'])
    return gpd.read_file(taz_path)


def download_blocks(year,state_str, county_ids):
    year_2_digit = str(year)[-2:]
    # Example URL format for reference:
    # https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_53_tabblock20.zip
    url = (
        f"https://www2.census.gov/geo/tiger/TIGER{year}/"
        f"TABBLOCK{year_2_digit}/"
        f"tl_{year}_{state_str}_tabblock{year_2_digit}.zip"
    )
    print(f"Downloading {year} blocks from {url}")

    geoid = f'GEOID{year_2_digit}'
    return (
        gpd.read_file(url)
        .assign(
            block_id=lambda df: ('1' + df[geoid]).astype('int64'),
            tract_id=lambda df: ('1' + df[geoid]).str[:12].astype('int64'),
            county_id=lambda df: ('1' + df[geoid].str[:5]).astype(int),
        )
        .query('county_id.isin(@county_ids)')
        [['block_id', 'geometry']]
    )


def taz_block_lookup(pipeline, taz):
    year = pipeline.context['year']

    all_results = []

    for state_str, county_ids in pipeline.state_county_fips.items():
        blocks = download_blocks(year, state_str, county_ids)

        # spatial join block centroids to TAZs
        blocks.geometry = blocks.representative_point()
        joined = gpd.sjoin(blocks, taz)
        joined['zone_id'] = joined['zone_id'].astype(int)
        joined['region'] = 1

        all_results.append(joined[['block_id', 'zone_id', 'region']])

    result = pd.concat(all_results, ignore_index=True)
    pipeline.save_table('taz_block_lookup', result, fmt='csv')
    

def run_step(context):
    pipeline = Pipeline(context)
    taz = load_taz_layer(pipeline)
    taz_block_lookup(pipeline, taz)
