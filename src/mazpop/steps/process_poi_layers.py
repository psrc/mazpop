import geopandas as gpd
import pandas as pd
from pathlib import Path

from mazpop.util.pipeline import Pipeline


def run_step(context):
    pipeline = Pipeline(context)
    today = pd.Timestamp.today().strftime("%Y-%m-%d")
    poi_layers = pipeline.settings.get('point_of_interest_layers', [])
    for poi_layer in poi_layers:
        layer_path = Path(pipeline.data_dir) / poi_layer['filename']
        gdf = gpd.read_file(layer_path)
        gdf = gdf.to_crs(epsg=4326)
        if poi_layer.get('polygon', False):
            gdf['geometry'] = gdf.representative_point()
        output_csv = Path(pipeline.output_dir) / f"{poi_layer['filename'].replace('.shp', f'_{today}.csv')}"
        gdf[['x','y']] = gdf[['geometry']].apply(lambda row: pd.Series((row.geometry.x, row.geometry.y)), axis=1)
        gdf[['x','y']].to_csv(output_csv, index=False)
    return context