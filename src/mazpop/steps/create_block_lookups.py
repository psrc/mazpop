import geopandas as gpd
from pathlib import Path
import pandas as pd
from mazpop.util.pipeline import Pipeline


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


def download_blocks(year,state_str, county_ids):
    year_2_digit = str(year)[-2:]
    if year == 2020:
        url_year_2_digit = year_2_digit
    else:
        url_year_2_digit = ''
    # Example 2020 URL format for reference:
    # https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK20/tl_2020_53_tabblock20.zip
    # Example 2010 URL format for reference:
    # https://www2.census.gov/geo/tiger/TIGER2020/TABBLOCK/tl_2020_53_tabblock10.zip
    url = (
        f"https://www2.census.gov/geo/tiger/TIGER2020/"
        f"TABBLOCK{url_year_2_digit}/"
        f"tl_2020_{state_str}_tabblock{year_2_digit}.zip"
    )
    print(f"Downloading {year} blocks from {url}")

    geoid = f'GEOID{year_2_digit}'
    blocks = (
        gpd.read_file(url)
        .assign(
            block_id=lambda df: ('1' + df[geoid]).astype('int64'),
            county_id=lambda df: ('1' + df[geoid].str[:5]).astype(int),
            acres=lambda df: df[f'ALAND{year_2_digit}'] / 4046.86  # Convert square meters to acres
        )
        .query('county_id.isin(@county_ids)')
        .to_crs(epsg=4326)
    )
    blocks.geometry = blocks.representative_point()
    blocks['x'] = blocks['geometry'].x
    blocks['y'] = blocks['geometry'].y
    return blocks[['block_id', 'acres', 'x', 'y', 'geometry']]


def prepare_block_points(pipeline):
    year = pipeline.context['year']
    all_blocks = []
    for state_str, county_ids in pipeline.state_county_fips.items():
            blocks = download_blocks(year, state_str, county_ids)
            all_blocks.append(blocks)
    return pd.concat(all_blocks, ignore_index=True)


def spatial_join_blocks_to_geog(block_pts, geog, geog_id, additional_columns=[]):
    geog = geog.to_crs(epsg=4326)
    joined = gpd.sjoin(block_pts, geog, how='left')
    return joined[['block_id', geog_id] + additional_columns]


def get_geog_id(layer):
    if layer.get('rename_id_field', None):
        return layer['rename_id_field']
    else:
        return layer['input_id_field']


def drop_blocks_not_in_clip_layers(pipeline,blocks):
    """For any layers that are marked to clip blocks, drop blocks that do not have a corresponding value in those layers."""
    clip_layer_id_cols = []
    for layer in pipeline.settings['spatial_layers']:
        if layer.get('clip_blocks_to_layer', False):
            clip_layer_id_cols.append(get_geog_id(layer))
    if clip_layer_id_cols:
        blocks = blocks.dropna(subset=clip_layer_id_cols)
        print(f"Clipping blocks to layers with ID columns: {clip_layer_id_cols}")
    return blocks


def run_step(context):
    pipeline = Pipeline(context)
    year = pipeline.context['year']
    blocks = prepare_block_points(pipeline)
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
    pipeline.save_table(f'blocks_{year}', blocks_with_geog, fmt='csv')
    return context
