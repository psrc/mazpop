from multiprocessing import context

import pandas as pd

from mazpop.util.census_api import (
    CensusAPI,
    build_aggregated_variables_by_group,
    build_variables_by_group,
    build_totals_variables_dict,
    check_totals,
    columns_to_int,
)
from mazpop.util.pipeline import Pipeline


def totals_data(pipeline):
    acs_year = pipeline.context['acs_year']
    pipeline.create_directory(path=str(pipeline.pipeline_dir / f'acs_data_{acs_year}'))
    census_api = CensusAPI(pipeline.CENSUS_KEY)
    print('Downloading ACS totals')
    variables_dict = build_totals_variables_dict(pipeline.get_acs_config_dir(acs_year))
    try:
        dfs = []
        for state_id, county_ids in pipeline.state_county_fips.items():
            state_df = census_api.get_acs_data(
                variables_dict=variables_dict,
                year=acs_year,
                geog='tract',
                dataset='acs5',
                county_ids=county_ids,
                state_id=state_id,
            )
            dfs.append(state_df)
        df = pd.concat(dfs, ignore_index=True)
    except Exception as e:
        raise RuntimeError(f"Failed to download ACS totals: {e}") from e
    
    df = columns_to_int(df)
    pipeline.save_table(f'acs_data_{acs_year}/acs_totals', df, fmt='csv')
    return df


def get_selected_groups(pipeline):
    """Return the variables-by-group dict for the groups to download.

    Honors ``marginals_groups.yaml`` (selected + ``always_download`` groups)
    when present, otherwise falls back to every group in the config CSV.
    """
    year_key = pipeline.context['year_key'] # "base" or "history"
    yaml_path = pipeline.settings_path / f'{year_key}_marginals_groups.yaml'
    acs_year = pipeline.context['acs_year']
    if yaml_path.exists():
        print(f"Using ACS marginals groups from {yaml_path}")
        return build_aggregated_variables_by_group(
            pipeline.get_acs_config_dir(acs_year), 'marginals_expressions.csv', yaml_path
        )
    return build_variables_by_group(pipeline.get_acs_config_dir(acs_year), 'marginals_expressions.csv')


def acs_data(pipeline,totals_df):
    census_api = CensusAPI(pipeline.CENSUS_KEY, timeout=300)
    acs_year = pipeline.context['acs_year']
    pipeline.create_directory(path=str(pipeline.pipeline_dir / f'acs_data_{acs_year}'))
    groups = get_selected_groups(pipeline)

    for group_name, variables_dict in groups.items():
        print(f'Downloading ACS group: {group_name}')
        try:
            dfs = []
            for state_id, county_ids in pipeline.state_county_fips.items():
                state_df = census_api.get_acs_data(
                    variables_dict=variables_dict,
                    year=acs_year,
                    geog='tract',
                    dataset='acs5',
                    county_ids=county_ids,
                    state_id=state_id,
                )
                dfs.append(state_df)
            group_df = pd.concat(dfs, ignore_index=True)
        except Exception as e:
            raise RuntimeError(f"Failed to download ACS group '{group_name}': {e}") from e
        group_df = columns_to_int(group_df)
        pipeline.save_table(f'acs_data_{acs_year}/{group_name}', group_df, fmt='csv')
        check_totals(totals_df, group_name, group_df, pipeline.get_acs_config_dir(acs_year) / 'marginals_expressions.csv')


def evaluate_acs_expressions(pipeline):
    acs_year = pipeline.context['acs_year']
    marginals = pd.read_csv(pipeline.get_acs_config_dir(acs_year) / 'marginals_expressions.csv')
    calculated_expression = marginals['calculated_expression']
    expr_rows = marginals[
        calculated_expression.notna()
        & (calculated_expression.astype(str).str.strip() != '')
    ]

    if expr_rows.empty:
        return

    # Only evaluate expressions for groups that were actually downloaded.
    selected_groups = set(get_selected_groups(pipeline))
    expr_rows = expr_rows[expr_rows['group'].isin(selected_groups)]

    if expr_rows.empty:
        return

    totals = pipeline.get_table(f'acs_data_{acs_year}/acs_totals').set_index('geoid').drop(columns='name')

    for group_name, group_expr_rows in expr_rows.groupby('group'):
        print(f'Evaluating expressions for group: {group_name}')
        group_df = pipeline.get_table(f'acs_data_{acs_year}/{group_name}')
        name_col = group_df['name']
        df = group_df.set_index('geoid').drop(columns='name')

        # Strip group prefix so expressions can reference short names
        prefix = f'{group_name}_'
        df.columns = [c[len(prefix):] if c.startswith(prefix) else c for c in df.columns]

        for _, row in group_expr_rows.iterrows():
            col_name = str(row['name'])
            expr = row['calculated_expression'].strip()
            df[col_name] = eval(expr)  # noqa: S307 — trusted package config

        # Re-add group prefix
        df.columns = [f'{prefix}{c}' for c in df.columns]
        df = columns_to_int(df.reset_index())
        df.insert(1, 'name', name_col.values)
        pipeline.save_table(f'acs_data_{acs_year}/{group_name}', df, fmt='csv')


def run_step(context):
    # Initialize pipeline and census API
    pipeline = Pipeline(context)
    totals_df = totals_data(pipeline)
    acs_data(pipeline,totals_df)
    evaluate_acs_expressions(pipeline)
    return context