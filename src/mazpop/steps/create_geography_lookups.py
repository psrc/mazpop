"""Create the block and PUMA geography lookup tables in a single pass.

Combines the former ``create_block_lookups``, ``create_puma_tract_lookup``, and
``create_puma_block_lookup`` steps so each TIGER/Line file is downloaded once
and the parsed GeoDataFrames are reused for every table:

- ``blocks_<year>``: blocks with the configured spatial layer columns and
  ``maz_id`` (pipeline output dir).
- ``puma_tract_lookup.csv``: tract -> PUMA/region lookup (popsim data dir).
- ``puma_block_lookup.csv``: block -> tract/PUMA/region crosswalk used by
  PopulationSim as ``geo_cross_walk`` (popsim data dir).
"""

import re
from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
import us

from mazpop.util.pipeline import Pipeline


# ---------------------------------------------------------------------------
# TIGER/Line downloads
# ---------------------------------------------------------------------------


@lru_cache(maxsize=None)
def download_tiger_layer(url):
    """Download and parse a TIGER/Line zip, caching each URL.

    The cached GeoDataFrame is shared between callers, so callers must derive a
    new frame (with ``assign``/``to_crs``/``query``) before modifying it.
    """
    print(f"Downloading {url}")
    return gpd.read_file(url)


def download_blocks(year, state_str, county_ids):
    """Download TIGER/Line blocks for a state and return block points.

    Both block vintages are published in TIGER2020, so the URL is pinned to
    that release, e.g.:
    https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_53_tabblock20.zip
    https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK/tl_2020_53_tabblock10.zip
    """
    year_2_digit = str(year)[-2:]
    url_year_2_digit = year_2_digit if year == 2020 else ''
    geoid = f'GEOID{year_2_digit}'
    url = (
        f"https://www2.census.gov/geo/tiger/TIGER2020/"
        f"TABBLOCK{url_year_2_digit}/"
        f"tl_2020_{state_str}_tabblock{year_2_digit}.zip"
    )
    blocks = (
        download_tiger_layer(url)
        .assign(
            block_id=lambda df: ('1' + df[geoid]).astype('int64'),
            county_id=lambda df: ('1' + df[geoid].str[:5]).astype(int),
            acres=lambda df: df[f'ALAND{year_2_digit}'] / 4046.86,  # sq meters to acres
        )
        .query('county_id.isin(@county_ids)')
        .to_crs(epsg=4326)
    )
    blocks.geometry = blocks.representative_point()
    blocks['x'] = blocks['geometry'].x
    blocks['y'] = blocks['geometry'].y
    duplicated_blocks = blocks.loc[blocks['block_id'].duplicated()]
    print(f"Dropping {len(duplicated_blocks)} duplicated blocks for {year} blocks")
    blocks = blocks.drop_duplicates(subset=['block_id'])
    return blocks[['block_id', 'acres', 'x', 'y', 'geometry']]


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
    """Download TIGER/Line PUMAs for a state and return PUMA polygons."""
    puma_geog_year_two_digit = str(puma_geog_year)[-2:]
    puma_dir = get_puma_dir(puma_tiger_year, puma_geog_year_two_digit, state_str)
    two_digit_suffix = puma_geog_year_two_digit if puma_geog_year >= 2010 else '500'
    geoid = get_puma_geoid_column(puma_geog_year, puma_geog_year_two_digit)

    puma_url = (
        f"https://www2.census.gov/geo/tiger/TIGER{puma_tiger_year}/"
        f"{puma_dir}/"
        f"tl_{puma_tiger_year}_{state_str}_puma{two_digit_suffix}.zip"
    )
    return (
        download_tiger_layer(puma_url)
        .assign(puma_id=lambda df: ('1' + df[geoid].str.zfill(7)).astype(int))
        [['puma_id', 'geometry']]
    )


def download_tracts(tract_tiger_year, state_str, county_ids):
    """Download TIGER/Line tracts for a state and return tract polygons."""
    tract_url = (
        f"https://www2.census.gov/geo/tiger/TIGER{tract_tiger_year}/"
        f"TRACT/"
        f"tl_{tract_tiger_year}_{state_str}_tract.zip"
    )
    return (
        download_tiger_layer(tract_url)
        .assign(
            tract_id=lambda df: ('1' + df['GEOID']).astype('int64'),
            county_id=lambda df: ('1' + df['GEOID'].str[:5]).astype(int),
        )
        .query('county_id.isin(@county_ids)')
        [['tract_id', 'geometry']]
    )


# ---------------------------------------------------------------------------
# Spatial layers (local shapefiles joined to blocks)
# ---------------------------------------------------------------------------


def load_spatial_layer(pipeline, layer):
    rename_id_field = layer.get('rename_id_field', None)
    additional_columns = layer.get('additional_columns', [])
    layer_path = Path(pipeline.get_data_dir()) / layer['filename']
    geog = gpd.read_file(layer_path)
    if rename_id_field:
        geog = geog.rename(columns={layer['input_id_field']: rename_id_field})
    else:
        rename_id_field = layer['input_id_field']
    geog = geog.to_crs(epsg=4326)
    return geog[[rename_id_field, 'geometry'] + additional_columns]


def spatial_join_blocks_to_geog(block_pts, geog, geog_id, additional_columns=[]):
    geog = geog.to_crs(epsg=4326)
    joined = gpd.sjoin(block_pts, geog, how='left')
    return joined[['block_id', geog_id] + additional_columns]


def get_geog_id(layer):
    if layer.get('rename_id_field', None):
        return layer['rename_id_field']
    else:
        return layer['input_id_field']


def drop_blocks_not_in_clip_layers(pipeline, blocks):
    """For any layers that are marked to clip blocks, drop blocks that do not have a corresponding value in those layers."""
    clip_layer_id_cols = []
    for layer in pipeline.settings['spatial_layers']:
        if layer.get('clip_blocks_to_layer', False):
            clip_layer_id_cols.append(get_geog_id(layer))
    if clip_layer_id_cols:
        blocks = blocks.dropna(subset=clip_layer_id_cols)
        print(f"Clipping blocks to layers with ID columns: {clip_layer_id_cols}")
    return blocks


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------


def build_puma_tract_lookup(tracts_by_state, pumas_by_state):
    """Spatially join tract representative points to PUMA polygons."""
    all_results = []
    for state_str, tracts in tracts_by_state.items():
        tracts = tracts.copy()
        tracts.geometry = tracts.representative_point()
        joined = tracts.sjoin(pumas_by_state[state_str], how='left')
        joined['puma_id'] = joined['puma_id'].astype(int)
        joined['region'] = 1
        all_results.append(joined[['tract_id', 'puma_id', 'region']])

    return pd.concat(all_results, ignore_index=True)


def build_puma_block_lookup(blocks, puma_tract_lookup):
    """Build the block -> tract/PUMA crosswalk from the downloaded blocks.

    The TIGER blocks cover every block returned by the decennial API, so the
    block/tract/county ids come from the blocks GeoDataFrame already in memory
    instead of being downloaded again via the API-backed ``dec_totals`` table.
    """
    blocks = blocks[['block_id']].drop_duplicates()
    blocks = blocks.assign(
        tract_id=blocks['block_id'].astype(str).str[:12].astype('int64'),
        county_id=blocks['block_id'].astype(str).str[:6].astype('int64'),
    )
    result = blocks.merge(puma_tract_lookup, on='tract_id', how='left')
    return result[['block_id', 'tract_id', 'county_id', 'puma_id', 'region']]


def build_blocks_table(pipeline, blocks):
    """Add the configured spatial layer columns to blocks and save ``blocks_<year>``."""
    year = pipeline.context['year']
    joined_out = pd.DataFrame({'block_id': []})
    for layer in pipeline.settings['spatial_layers']:
        geog_id = get_geog_id(layer)
        print(f"Adding {geog_id} from {layer['filename']} to blocks")
        layer_geog = load_spatial_layer(pipeline, layer)
        joined = spatial_join_blocks_to_geog(
            block_pts=blocks,
            geog=layer_geog,
            geog_id=geog_id,
            additional_columns=layer.get('additional_columns', []),
        )
        joined_out = joined_out.merge(joined, on='block_id', how='outer')
    clipped_blocks = drop_blocks_not_in_clip_layers(pipeline, joined_out)
    blocks_with_geog = clipped_blocks.merge(blocks, on='block_id', how='left').drop(columns=['geometry'], errors='ignore')
    blocks_with_geog['maz_id'] = range(1, len(blocks_with_geog) + 1)
    pipeline.save_table(f'blocks_{year}', blocks_with_geog, fmt='csv')
    return blocks_with_geog


def run_step(context):
    pipeline = Pipeline(context)
    year = pipeline.context['year']
    pums_year = pipeline.context['acs_year']

    # Download each TIGER/Line file once and keep the GeoDataFrames in memory
    # while every table below is built from them.
    blocks_by_state = {
        state_str: download_blocks(year, state_str, county_ids)
        for state_str, county_ids in pipeline.state_county_fips.items()
    }

    puma_geog_year = get_census_geography_year(pums_year)
    puma_tiger_year = get_puma_tiger_year(puma_geog_year)
    tract_tiger_year = get_tract_tiger_year(pums_year)

    pumas_by_state = {
        state_str: download_pumas(puma_tiger_year, puma_geog_year, state_str)
        for state_str in pipeline.state_county_fips
    }
    tracts_by_state = {
        state_str: download_tracts(tract_tiger_year, state_str, county_ids)
        for state_str, county_ids in pipeline.state_county_fips.items()
    }

    blocks = pd.concat(blocks_by_state.values(), ignore_index=True)
    popsim_data_dir = Path(pipeline.get_popsim_root_dir()) / 'data'
    pipeline.create_directory(path=str(popsim_data_dir))

    print("Creating PUMA <-> tract lookup table...")
    puma_tract_lookup = build_puma_tract_lookup(tracts_by_state, pumas_by_state)
    puma_tract_lookup.to_csv(popsim_data_dir / 'puma_tract_lookup.csv', index=False)

    print("Creating PUMA <-> block lookup table...")
    puma_block_lookup = build_puma_block_lookup(blocks, puma_tract_lookup)
    puma_block_lookup.to_csv(popsim_data_dir / 'puma_block_lookup.csv', index=False)

    print("Creating block lookups...")
    build_blocks_table(pipeline, blocks)
    return context
