import geopandas as gpd
import pandas as pd
import requests
import re
from functools import lru_cache
import osmnx as ox
import networkx as nx
import os

from mazpop.util.pipeline import Pipeline
from mazpop.steps.create_geography_lookups import get_tract_tiger_year, download_tracts


def create_osm_boundary(tracts_by_state,blocks):
    blocks['tract_id'] = blocks['block_id'].astype(str).str[:12].astype('int64')
    include_tracts = set(blocks['tract_id'].unique())
    all_results = []
    for state_str, tracts in tracts_by_state.items():
        tracts = tracts.copy()
        tracts = tracts[tracts['tract_id'].isin(include_tracts)]
        all_results.append(tracts)
    return (gpd.GeoDataFrame(pd.concat(all_results, ignore_index=True))
            .to_crs(epsg=5070)
            .buffer(600)
            .to_crs(epsg=4326)
            .union_all())


# overpass-api.de is often overloaded/blocked, so fall back through a list of mirrors;
# the endpoint that succeeds is kept at the front for later downloads in the same run
OVERPASS_ENDPOINTS = [
    "https://overpass.kumi.systems/api",
    "https://overpass-api.de/api",
    "https://overpass.private.coffee/api",
    "https://overpass.osm.jp/api",
]

# region-wide queries can take several minutes, so raise the 180 second default; this is
# sent to the server as the query timeout and used as the client-side read timeout
OVERPASS_TIMEOUT = 600  # seconds


def graph_from_boundary(boundary, custom_filter, retain_all=False):
    """Download an OSM network, rotating through Overpass mirrors until one succeeds."""
    # osmnx's own errors subclass ValueError; client timeouts and busy or error
    # responses from a public instance are usually transient or mirror specific
    last_error: Exception = RuntimeError("no Overpass endpoints configured")

    for _ in range(len(OVERPASS_ENDPOINTS)):
        endpoint = OVERPASS_ENDPOINTS[0]
        ox.settings.overpass_url = endpoint
        try:
            return ox.graph_from_polygon(
                boundary,
                custom_filter=custom_filter,
                retain_all=retain_all,
            )
        except (requests.exceptions.RequestException, ValueError) as e:
            print(f"OSM download failed on {endpoint}: {e}")
            last_error = e
            # deprioritize this endpoint so the next attempt/call starts elsewhere
            OVERPASS_ENDPOINTS.append(OVERPASS_ENDPOINTS.pop(0))

    raise last_error


def build_network(boundary, include_ferries=False):
    # region-wide queries are slow: raise the default 180 second timeout and skip the
    # rate limit status check that mirrors don't expose
    ox.settings.requests_timeout = OVERPASS_TIMEOUT
    ox.settings.overpass_rate_limit = False

    drive_filter = (
        '["highway"]["area"!~"yes"]["access"!~"private"]'
        '["highway"!~"abandoned|bridleway|bus_guideway|construction|corridor|cycleway|elevator|'
        'escalator|footway|no|path|pedestrian|planned|platform|proposed|raceway|razed|service|steps|track"]'
        '["motor_vehicle"!~"no"]["motorcar"!~"no"]'
        '["service"!~"alley|driveway|emergency_access|parking|parking_aisle|private"]'
    )

    if include_ferries:
        # download the drive network plus car-ferry routes in one query so island
        # road networks stay connected to the mainland through ferry terminals
        ferry_filter = '["route"="ferry"]["motor_vehicle"!~"no"]'  # excludes passenger-only ferries

        # ferry routes cross open water and get truncated at the edge of the study
        # area, stranding islands that are only reachable by ferry. osmnx keeps only
        # the largest connected piece by default, so retain all pieces here and only
        # drop the genuinely stranded ones below
        G = graph_from_boundary(
            boundary,
            custom_filter=[drive_filter, ferry_filter],
            retain_all=True,
        )

        # sanity checks: ferries present, few one-way artifacts
        ferry_edge_count = sum(1 for *_, d in G.edges(data=True) if 'highway' not in d)
        print(f"ferry edges: {ferry_edge_count}")

        # keep the largest network plus any piece that still contains ferry edges,
        # i.e. islands whose ferry link to the mainland was truncated at the boundary
        components = list(nx.weakly_connected_components(G))
        keep = set(max(components, key=len))
        for component in components:
            if any('highway' not in d for *_, d in G.subgraph(component).edges(data=True)):
                keep |= component
        print(f"connected pieces: kept {sum(c <= keep for c in components)} of {len(components)} "
              f"(islands reachable only by ferry are retained)")
        G = G.subgraph(keep).copy()
    else:
        # without ferry routes osmnx keeps only the largest connected network and
        # drops any other unconnected pieces, which is fine
        G = graph_from_boundary(boundary, custom_filter=drive_filter)

        # sanity checks: single connected piece, few one-way artifacts
        print(f"weakly connected (no stranded pieces): {nx.is_weakly_connected(G)}")

    largest_scc = max(nx.strongly_connected_components(G), key=len)
    print(f"nodes outside largest strongly connected component "
        f"(one-way dead ends): {len(G) - len(largest_scc)} of {len(G)}")

    # convert graph to pandana-style nodes/edges dataframes
    nodes, edges = ox.graph_to_gdfs(G)

    nodes = (nodes
            .reset_index()
            .rename(columns={'osmid': 'id'})
            [['id', 'x', 'y']])

    edges = (edges
            .reset_index()
            .rename(columns={'u': 'from', 'v': 'to', 'length': 'weight', 'highway': 'edge_type'})
            # simplified edges can carry a list of highway tags; keep the first
            .assign(edge_type=lambda df: df['edge_type']
                    .map(lambda v: v[0] if isinstance(v, list) else v)))

    if include_ferries:
        # scale ferry weights so a ferry meter "costs" the same time as a driving meter
        # (WSF sails ~17 knots vs ~35 mph average driving)
        DRIVE_SPEED_MPH = 35
        FERRY_SPEED_MPH = 17 * 1.15078  # knots -> mph
        FERRY_SCALE = DRIVE_SPEED_MPH / FERRY_SPEED_MPH
        # wait/loading time per crossing, expressed as driving-equivalent meters
        FERRY_WAIT_MIN = 30
        FERRY_WAIT_PENALTY = DRIVE_SPEED_MPH * (FERRY_WAIT_MIN / 60) * 1609.344

        edges = (edges
                # ferry ways have no highway tag
                .assign(edge_type=lambda df: df['edge_type'].fillna('ferry'))
                .assign(weight=lambda df: df['weight']
                        .where(df['edge_type'] != 'ferry',
                               df['weight'] * FERRY_SCALE + FERRY_WAIT_PENALTY)))

    edges = edges[['from', 'to', 'weight', 'edge_type']]

    return edges, nodes

def run_step(context):
    pipeline = Pipeline(context)
    
    # download tracts, filter to clipped blocks and create bounding shape for the OSM network
    year = context['year']
    tract_tiger_year = get_tract_tiger_year(year)
    tracts_by_state = {
        state_str: download_tracts(tract_tiger_year, state_str, county_ids)
        for state_str, county_ids in pipeline.state_county_fips.items()
    }
    blocks = pipeline.get_table(f'blocks_{year}')
    boundary = create_osm_boundary(tracts_by_state, blocks)

    # build the OSM network
    print("Building OSM network")
    edges, nodes = build_network(boundary, include_ferries=pipeline.settings['include_ferries'])

    # export network to csv
    print(f"Exporting OSM network to edges and nodes CSVs in {pipeline.output_dir}")
    today = pd.Timestamp.today().strftime('%Y-%m-%d')
    nodes.to_csv(f'{pipeline.output_dir}/nodes_{today}.csv', index=False)
    edges.to_csv(f'{pipeline.output_dir}/edges_{today}.csv', index=False)

    return context