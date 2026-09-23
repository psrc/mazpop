import re
from functools import lru_cache

import geopandas as gpd
import pandas as pd
from pathlib import Path
import requests
import us

from mazpop.util.pipeline import Pipeline


def get_census_geography_year(pums_year):
    """Map a PUMS data year to the PUMA geography vintage it is coded to."""
    if 2023 <= pums_year <= 2032:
        return 2020
    elif pums_year == 2022:
        raise ValueError(
            "PUMS data for 2022 is split between 2010 and 2020 PUMA geographies. "
            "Please choose either 2021 or 2023 for pums_year."
        )
    elif 2016 <= pums_year <= 2021:
        return 2010
    elif 2012 <= pums_year < 2016:
        raise ValueError(
            "PUMS data for 2012-2015 is split between 2000 and 2010 PUMA geographies. "
            "Please choose either 2011 or 2016 for pums_year."
        )
    elif 2009 <= pums_year < 2012:
        return 2000
    else:
        raise ValueError("PUMS year out of range.")


@lru_cache(maxsize=1)
def get_most_recent_tiger_year():
    response = requests.get("https://www2.census.gov/geo/tiger/")
    folders = re.findall(r'TIGER\d{4}/', response.text)
    return max(int(f[5:9]) for f in folders)


def get_puma_tiger_year(puma_geog_year):
    """2010-vintage PUMA/tract shapefiles were dropped from later TIGER releases, so pin to TIGER2019."""
    if puma_geog_year > 2019:
        return get_most_recent_tiger_year()
    elif puma_geog_year > 2009:
        return 2019
    elif puma_geog_year >= 2000:
        return 2009
    else:
        raise ValueError(f"Unsupported PUMA geography year: {puma_geog_year}")


def get_tract_tiger_year(pums_year):
    if pums_year >= 2020:
        return get_most_recent_tiger_year()
    elif pums_year >= 2010:
        return 2019
    elif pums_year >= 2000:
        return 2009
    else:
        raise ValueError(f"Unsupported PUMS year: {pums_year}")


def get_puma_dir(puma_tiger_year, puma_geog_year_two_digit, state_str):
    # TIGER2019 (used for the 2010 vintage) has an unsuffixed PUMA/ folder; later releases suffix it, e.g. PUMA20/
    if puma_tiger_year >= 2020:
        return f"PUMA{puma_geog_year_two_digit}"
    elif puma_tiger_year == 2019:
        return "PUMA"
    elif puma_tiger_year == 2009:
        state = us.states.lookup(state_str)
        return f"{state.fips}_{state.name.upper()}"
    else:
        raise ValueError(f"Unsupported PUMA TIGER year: {puma_tiger_year}")


def get_puma_geoid_column(puma_geog_year, puma_geog_year_two_digit):
    return f"GEOID{puma_geog_year_two_digit}" if puma_geog_year >= 2010 else "PUMA5ID00"


def download_pumas(puma_tiger_year, puma_geog_year, state_str):
    puma_geog_year_two_digit = str(puma_geog_year)[-2:]
    puma_dir = get_puma_dir(puma_tiger_year, puma_geog_year_two_digit, state_str)
    two_digit_suffix = puma_geog_year_two_digit if puma_geog_year >= 2010 else '500'
    geoid = get_puma_geoid_column(puma_geog_year, puma_geog_year_two_digit)

    puma_url = (
        f"https://www2.census.gov/geo/tiger/TIGER{puma_tiger_year}/"
        f"{puma_dir}/"
        f"tl_{puma_tiger_year}_{state_str}_puma{two_digit_suffix}.zip"
    )
    print(f"Downloading {puma_geog_year} PUMAs from {puma_url}")

    return (
        gpd.read_file(puma_url)
        .assign(puma_id=lambda df: ('1' + df[geoid].str.zfill(7)).astype(int))
        [['puma_id', 'geometry']]
    )


def download_tracts(tract_tiger_year, state_str, county_ids):
    tract_url = (
        f"https://www2.census.gov/geo/tiger/TIGER{tract_tiger_year}/"
        f"TRACT/"
        f"tl_{tract_tiger_year}_{state_str}_tract.zip"
    )
    print(f"Downloading {tract_tiger_year} tracts from {tract_url}")

    return (
        gpd.read_file(tract_url)
        .assign(
            tract_id=lambda df: ('1' + df['GEOID']).astype('int64'),
            county_id=lambda df: ('1' + df['GEOID'].str[:5]).astype(int),
        )
        .query('county_id.isin(@county_ids)')
        [['tract_id', 'geometry']]
    )


def create_puma_tract_lookup(pipeline):
    pums_year = pipeline.context['acs_year']
    puma_geog_year = get_census_geography_year(pums_year)
    puma_tiger_year = get_puma_tiger_year(puma_geog_year)
    tract_tiger_year = get_tract_tiger_year(pums_year)

    all_results = []

    for state_str, county_ids in pipeline.state_county_fips.items():
        puma = download_pumas(puma_tiger_year, puma_geog_year, state_str)
        tracts = download_tracts(tract_tiger_year, state_str, county_ids)

        # spatial join tract centroids to PUMAs
        tracts.geometry = tracts.representative_point()
        joined = tracts.sjoin(puma, how='left')
        joined['puma_id'] = joined['puma_id'].astype(int)
        joined['region'] = 1

        all_results.append(joined[['tract_id', 'puma_id', 'region']])

    result = pd.concat(all_results, ignore_index=True)
    out_path = Path.joinpath(pipeline.get_popsim_root_dir(), 'data')
    result.to_csv(out_path / 'puma_tract_lookup.csv', index=False)


def run_step(context):
    print("Creating PUMA <-> tract lookup table...")
    pipeline = Pipeline(context)
    create_puma_tract_lookup(pipeline)
    return context
