import ast
from importlib import resources
from pathlib import Path

import pandas as pd
import requests

_GROUP_TOTALS_PATH = Path(str(resources.files('mazpop.configs'))) / 'group_totals.csv'


def check_totals(totals_df, group_name, group_df,expressions_path):
    """Check that a group's data columns sum to the expected total per tract.

    Parameters
    ----------
    totals_df : DataFrame
        Totals table with a 'geoid' column (e.g. acs_totals or dec_totals).
    group_name : str
        Name of the group (must appear in group_totals.csv).
    group_df : DataFrame
        The group data table with 'geoid' and 'name' columns.
    expressions_path : str
        Path to the expressions file (e.g. marginals_expressions.csv).
    """
    group_totals = pd.read_csv(_GROUP_TOTALS_PATH)
    match = group_totals[group_totals['group'] == group_name]
    if match.empty:
        return

    total_col = match.iloc[0]['total']
    totals = totals_df.set_index('geoid')[total_col]

    data = group_df.set_index('geoid').drop(columns='name')
    row_sums = data.sum(axis=1)

    diff = row_sums - totals
    mismatches = diff[diff != 0]
    if not mismatches.empty:
        raise ValueError(
            f"'{group_name}' does not match '{total_col}': "
            f"{len(mismatches)} tracts differ (max diff: {mismatches.abs().max()})"
            f"\nCheck that the variables in {expressions_path} correctly match the Census API variables."
        )


def columns_to_int(df):
    for col in df.columns:
        if col not in ['geoid', 'name']:
            df[col] = df[col].astype(int)
    return df


def build_variables_by_group(config_dir, filename):
    marginals = pd.read_csv(config_dir / filename)
    groups = {}
    for _, row in marginals.iterrows():
        vars_val = row['variables']
        if pd.notna(vars_val) and str(vars_val).strip():
            group = row['group']
            col = f'{group}_{row["name"]}'
            groups.setdefault(group, {})[col] = ast.literal_eval(str(vars_val).strip())
    return groups


def build_totals_variables_dict(config_dir, filename='total_variables.csv'):
    totals = pd.read_csv(config_dir / filename)
    return {row['name']: ast.literal_eval(str(row['variables']).strip()) for _, row in totals.iterrows()}


def build_aggregated_variables_by_group(config_dir, filename, yaml_path):
    """Build variables_dict per group, aggregating bins per marginals_groups.yaml.

    Groups in the YAML have their census variables merged according to bin
    definitions.  Groups marked ``always_download=TRUE`` in the CSV that are
    absent from the YAML are included 1:1.  All other groups are skipped.

    Returns ``{group_name: {output_col: [census_vars, ...], ...}, ...}``
    """
    import yaml

    marginals = pd.read_csv(config_dir / filename)

    # Build lookup: (group, name) -> variables list (only rows with variables)
    name_to_vars = {}
    group_names = {}
    for _, row in marginals.iterrows():
        vars_val = row['variables']
        group = row['group']
        name = str(row['name'])
        if pd.notna(vars_val) and str(vars_val).strip():
            name_to_vars[(group, name)] = ast.literal_eval(str(vars_val).strip())
            group_names.setdefault(group, []).append(name)

    # Identify always_download groups
    always_download_groups = set()
    if 'always_download' in marginals.columns:
        for _, row in marginals.iterrows():
            val = row.get('always_download', '')
            if pd.notna(val) and str(val).strip().upper() == 'TRUE':
                always_download_groups.add(row['group'])

    # Load YAML
    with open(yaml_path, 'r') as f:
        yaml_groups = yaml.safe_load(f) or {}

    result = {}

    # Process groups defined in YAML
    for group_name, bins_list in yaml_groups.items():
        if group_name not in group_names:
            continue
        variables_dict = {}
        for bin_item in bins_list:
            for bin_name, suffixes in bin_item.items():
                output_col = f'{group_name}_{bin_name}'
                merged_vars = []
                for suffix in suffixes:
                    name = str(suffix)
                    if (group_name, name) in name_to_vars:
                        merged_vars.extend(name_to_vars[(group_name, name)])
                if merged_vars:
                    variables_dict[output_col] = merged_vars
        if variables_dict:
            result[group_name] = variables_dict

    # Add always_download groups not already in YAML
    for group in always_download_groups:
        if group not in result and group in group_names:
            variables_dict = {}
            for name in group_names[group]:
                if (group, name) in name_to_vars:
                    variables_dict[f'{group}_{name}'] = name_to_vars[(group, name)]
            if variables_dict:
                result[group] = variables_dict

    return result


class CensusAPI:
    def __init__(self, api_key, timeout=15):
        self.api_key = api_key
        self.timeout = timeout

    def get_table(self, variables, year, for_predicates, in_predicates, dataset_url):
        """
        Takes in a list of variables and returns a dataframe
        """
        HOST = "https://api.census.gov/data"
        base_url = "/".join([HOST, str(year), dataset_url])
        chunks = [variables[x:x+45] for x in range(0, len(variables), 45)]
        df = pd.DataFrame()
        for chunk in chunks:
            predicates = {}
            predicates["get"] = ",".join(chunk)
            predicates["for"] = for_predicates
            if in_predicates is not None:
                predicates["in"] = in_predicates
            predicates["key"] = self.api_key
            r = requests.get(base_url, params=predicates, timeout=self.timeout)
            chunk_df = pd.DataFrame(r.json()[1:], columns=r.json()[0])
            if df.empty:
                df = chunk_df
            else:
                df.drop(columns=['state', 'county', 'tract'], inplace=True, errors='ignore')
                df = df.merge(chunk_df, left_index=True, right_index=True)
        return df

    @staticmethod
    def combine_groups(variables_dict, df):
        """
        Takes in a dictionary of variables and a dataframe and sums any variables that 
        are made up of multiple census columns.
        """
        for key, value in variables_dict.items():
            df[key] = df[value].astype(float).sum(axis=1)
            df = df.drop(value, axis=1)
        return df

    @staticmethod
    def create_in_predicates(geog, county_ids, state_id):
        """
        Takes in a geography and returns in_predicates
        """
        county_ids_str = [str(county_id)[3:] for county_id in county_ids]
        if geog in ['tract', 'block group', 'block']:
            counties_str = ','.join(county_ids_str)
            in_predicates = f'state:{str(state_id)}', f'county:{counties_str}'
        elif geog in ['county', 'place', 'congressional district']:
            in_predicates = f'state:{str(state_id)}'
        elif geog == 'state':
            in_predicates = None
        else:
            raise ValueError("geog must be: 'state', 'county', 'congressional district', 'place', 'tract', 'block group' or 'block'")
        return in_predicates

    def get_census_data(self, variables_dict, year, geog, data_product, dataset, county_ids, state_id):
        """
        Takes in a dictionary of variables and returns decennial data in a dataframe.
        """
        in_predicates = self.create_in_predicates(geog, county_ids, state_id)
        for_predicates = f'{geog}:*'
        dataset_url = f'{data_product}/{dataset}'
        start_vars = ['GEO_ID', 'NAME']
        variables = [i for j in variables_dict.values() for i in j]
        variables = start_vars + variables
        df = self.get_table(variables, year, for_predicates, in_predicates, dataset_url)
        df = self.combine_groups(variables_dict, df)
        df = self.create_geoid(geog, df)
        df.rename(columns={'NAME': 'name'}, inplace=True)
        df = df[['geoid', 'name'] + list(variables_dict.keys())]
        return df

    def get_dec_data(self, variables_dict, year, geog, dataset, county_ids, state_id):
        return self.get_census_data(variables_dict, year, geog, 'dec', dataset, county_ids, state_id)
    
    def get_acs_data(self, variables_dict, year, geog, dataset, county_ids, state_id):
        return self.get_census_data(variables_dict, year, geog, 'acs', dataset, county_ids, state_id)

    def create_geoid(self, geog, df):
        geog_slices = {
            'block': -15,
            'tract': -11,
            'block group': -12,
            'county': -5,
            'place': -7,
            'state': -2
        }
        df['geoid'] = ('1' + df['GEO_ID'].str.slice(start=geog_slices[geog])).astype('int64')
        return df


CensusApi = CensusAPI
