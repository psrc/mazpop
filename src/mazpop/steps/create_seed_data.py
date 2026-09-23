import glob
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from mazpop.util.pipeline import Pipeline


def create_seed_data(pipeline):
    households = pd.DataFrame()
    persons = pd.DataFrame()
    year = pipeline.context['acs_year']
    for state_id_str in pipeline.state_county_fips:
        hh = pipeline.get_table(f'psam_h{state_id_str}_{year}')
        p = pipeline.get_table(f'psam_p{state_id_str}_{year}')
        households = pd.concat([households, hh], ignore_index=True)
        persons = pd.concat([persons, p], ignore_index=True)

    expressions = pd.read_csv(pipeline.get_acs_config_dir(year) / 'seed_data_expressions.csv')

    for _, row in expressions.iterrows():
        target = str(row['expr_out']).strip()
        expr = str(row['expression']).strip()

        result = eval(expr, {'__builtins__': {}}, {  # noqa: S307
            'households': households,
            'persons': persons,
            'np': np,
            'pd': pd,
            'round': round,
            'str': str,
            'int': int,
            'float': float,
        })

        if target == 'households':
            households = result
        elif target == 'persons':
            persons = result
        elif target.startswith('households.'):
            col = target.split('.', 1)[1]
            households[col] = result
        elif target.startswith('persons.'):
            col = target.split('.', 1)[1]
            persons[col] = result
        else:
            raise ValueError(f"Unknown expression target: {target}")

    targets = expressions['expr_out'].str.strip().unique()
    h_cols = [t.split('.', 1)[1] for t in targets if '.' in t and t.startswith('households.')]
    p_cols = [t.split('.', 1)[1] for t in targets if '.' in t and t.startswith('persons.')]
    households = households[[c for c in dict.fromkeys(h_cols) if c in households.columns]]
    persons = persons[[c for c in dict.fromkeys(p_cols) if c in persons.columns]]

    # use nullable Int64 so -1 sentinels can become NaN instead of staying as ints
    households = households.astype({c: 'Int64' for c in households.select_dtypes(include='int').columns})
    persons = persons.astype({c: 'Int64' for c in persons.select_dtypes(include='int').columns})

    # -1 is used as a sentinel for missing/not-applicable values in expressions
    households = households.replace(-1, pd.NA)
    persons = persons.replace(-1, pd.NA)

    year_key = pipeline.context['year_key']
    popsim_root_dir = pipeline.get_popsim_root_dir()
    popsim_data_dir = popsim_root_dir / 'data'
    households.to_csv(popsim_data_dir / 'seed_households.csv', index=False)
    persons.to_csv(popsim_data_dir / 'seed_persons.csv', index=False)
    print(f"State seed data created: {households['wgtp'].sum():,.0f} households, {persons['pwgtp'].sum():,.0f} persons for state id: {state_id_str}.")
    return households, persons


def _popsim_defaults_settings_path():
    return (
        Path(str(resources.files('mazpop.configs')))
        / 'popsim_defaults'
        / 'settings.yaml'
    )


def write_popsim_settings(pipeline, household_columns, person_columns):
    """Write the project popsim settings into ``popsim_configs/settings.yaml``.

    Starts from the packaged ``popsim_defaults/settings.yaml`` and overrides the
    ``output_synthetic_population`` household/person ``columns`` to match the
    columns produced by :func:`create_seed_data`.
    """
    with open(_popsim_defaults_settings_path(), 'r') as f:
        settings = yaml.safe_load(f)

    osp = settings.get('output_synthetic_population') or {}
    hh_id_col = osp.get('household_id')
    if 'households' in osp:
        osp['households']['columns'] = [c for c in household_columns if c != hh_id_col]
    if 'persons' in osp:
        osp['persons']['columns'] = [c for c in person_columns if c != hh_id_col]
    settings['output_synthetic_population'] = osp

    out_dir = pipeline.get_popsim_root_dir() / 'configs'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / 'settings.yaml'
    with open(out_path, 'w') as f:
        yaml.safe_dump(settings, f, sort_keys=False, default_flow_style=False)
    print(f"Wrote popsim settings to {out_path}")
    return out_path

def run_step(context):
    pipeline = Pipeline(context)
    households, persons = create_seed_data(pipeline)
    write_popsim_settings(pipeline, households.columns, persons.columns)
    return context
