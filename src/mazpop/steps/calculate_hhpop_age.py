from iteround import saferound
import pandas as pd
import yaml

from mazpop.util.pipeline import Pipeline


def compute_hhpop_rates(pipeline):
    """Load decennial county data, aggregate to region, and compute hhpop rates."""
    year = pipeline.context['year']
    county_df = pipeline.get_table(f'dec_data_{year}/county_data')

    # Filter to project counties
    all_county_ids = [cid for ids in pipeline.state_county_fips.values() for cid in ids]
    county_df = county_df[county_df['geoid'].isin(all_county_ids)]

    # Sum numeric columns to regional totals
    numeric_cols = county_df.select_dtypes(include='number').columns.drop('geoid')
    regional = county_df[numeric_cols].sum()

    # Read expression config for hhpop_rate_age_* rows
    config = pd.read_csv(pipeline.get_dec_config_dir(year) / 'county_data.csv')
    expr_rows = config[
        config['expression'].notna()
        & (config['expression'].str.strip() != '')
        & config['name'].str.startswith('hhpop_rate_age_')
    ]

    # Evaluate expressions and build rates dict {suffix: rate}
    rates = {}
    df = regional  # expressions reference 'df'
    for _, row in expr_rows.iterrows():
        suffix = row['name'].replace('hhpop_rate_age_', '', 1)
        value = eval(row['expression'])  # noqa: S307 — trusted package config
        if pd.isna(value):
            value = 0.0
        rates[suffix] = value

    return rates


def compute_hhpop_age(pipeline, rates):
    """Multiply ACS total_pop_age by hhpop rates to produce tract-level hhpop_age."""
    acs_year = pipeline.context['acs_year']
    total_pop_age = pipeline.get_table(f'acs_data_{acs_year}/total_pop_age')

    result = total_pop_age[['geoid', 'name']].copy()

    for suffix, rate in rates.items():
        src_col = f'total_pop_age_{suffix}'
        dst_col = f'hhpop_age_{suffix}'
        if src_col in total_pop_age.columns:
            result[dst_col] = (total_pop_age[src_col] * rate).round().astype(int)

    return result


def aggregate_hhpop_age(pipeline, df):
    """Aggregate hhpop_age columns per marginals_groups.yaml bins if available."""
    year_key = pipeline.context['year_key']
    yaml_path = pipeline.settings_path / f'{year_key}_marginals_groups.yaml'

    if not yaml_path.exists():
        return df

    with open(yaml_path, 'r') as f:
        yaml_groups = yaml.safe_load(f) or {}

    if 'hhpop_age' not in yaml_groups:
        return df

    bins_list = yaml_groups['hhpop_age']
    result = df[['geoid', 'name']].copy()

    for bin_item in bins_list:
        for bin_name, suffixes in bin_item.items():
            output_col = f'hhpop_age_{bin_name}'
            source_cols = [
                f'hhpop_age_{s}' for s in map(str, suffixes)
                if f'hhpop_age_{s}' in df.columns
            ]
            if source_cols:
                result[output_col] = df[source_cols].sum(axis=1).astype(int)

    return result

def normalize_hhpop_age(pipeline, df):
    acs_year = pipeline.context['acs_year']
    totals = pipeline.get_table(f'acs_data_{acs_year}/acs_totals').set_index('geoid')['hhpop']
    result = df[['geoid', 'name']].copy()
    df = df.set_index('geoid').drop(columns='name')
    # normalize hhpop_age columns to match total hhpop per tract
    df = df.div(df.sum(axis=1), axis=0).mul(totals, axis=0).fillna(0)
    # saferound to ensure rows sum to totals after rounding
    for geoid in df.index:
        df.loc[geoid] = saferound(df[df.index==geoid].unstack(),0)
    df = df.astype(int).reset_index()
    result = result.merge(df, on='geoid')
    return result

def determine_marginals_groups_file(pipeline,context):
    year_key = context['year_key']
    
    return pipeline.settings_path / f'marginals_groups_{year_key}.yaml'


def run_step(context):
    pipeline = Pipeline(context)
    acs_year = pipeline.context['acs_year']

    print('Computing hhpop rates from decennial county data')
    rates = compute_hhpop_rates(pipeline)

    print('Computing tract-level hhpop_age from ACS total_pop_age')
    hhpop_age = compute_hhpop_age(pipeline, rates)

    print('Aggregating hhpop_age per marginals_groups.yaml')
    hhpop_age = aggregate_hhpop_age(pipeline, hhpop_age)

    print('Normalizing hhpop_age to match total hhpop per tract')
    hhpop_age = normalize_hhpop_age(pipeline, hhpop_age)

    pipeline.save_table(f'acs_data_{acs_year}/hhpop_age', hhpop_age, fmt='csv')
