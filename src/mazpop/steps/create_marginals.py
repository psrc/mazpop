from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd
from iteround import saferound

from mazpop.util.pipeline import Pipeline


# Group prefixes, longest-first to avoid ambiguous matches like 'hhpop_age'
# vs 'hhpop_race'. Populated from the config expression files by
# _load_known_groups; _DEC_GROUPS records which of them are decennial.
_KNOWN_GROUPS = []
_DEC_GROUPS = set()

# Standalone (non-group) controls map a control target directly to a totals
# column instead of being scaled from an ACS category group. The mapping is
# loaded at runtime from total_variables.csv by _load_special_control_totals:
# each total name maps to itself, and the optional ``control_alias`` column adds
# names like num_hh -> hh that have no API variable of their own.

# Overrides to group_totals.csv mapping for this step. Defaults come from
# mazpop/configs/group_totals.csv.
GROUP_TOTAL_OVERRIDES = {
    'hhpop_race': 'hhpop',
    'hhpop_ethnicity': 'hhpop',
    # industry/occupation map to 'workers' in group_totals.csv but no decennial
    # workers count exists; we resolve 'workers' to ACS values at runtime.
}


def _load_known_groups(pipeline):
    """Populate the module-level group lists from the ACS/decennial configs."""
    acs_groups = _read_config_groups(
        Path(pipeline.get_acs_config_dir(pipeline.context['acs_year'])) / 'marginals_expressions.csv'
    )
    dec_groups = _read_config_groups(
        Path(pipeline.get_dec_config_dir(pipeline.context['year'])) / 'block_marginals_expressions.csv'
    )
    _DEC_GROUPS.clear()
    _DEC_GROUPS.update(dec_groups)
    _KNOWN_GROUPS[:] = sorted(acs_groups | dec_groups, key=len, reverse=True)


def _read_config_groups(path):
    if not path.exists():
        return set()
    df = pd.read_csv(path, dtype=str)
    return {str(g).strip() for g in df['group'].dropna() if str(g).strip()}


def _load_dec_total_columns(pipeline):
    """Return the decennial total column names (units, hh, hhpop, ...)."""
    path = Path(pipeline.get_dec_config_dir(pipeline.context['year'])) / 'total_variables.csv'
    df = pd.read_csv(path, dtype=str)
    return [str(n).strip() for n in df['name'].dropna() if str(n).strip()]


def _derive_group(target, special_controls):
    """Return the group name for a control target, or None for specials."""
    if target in special_controls:
        return None
    for grp in _KNOWN_GROUPS:
        if target == grp or target.startswith(grp + '_'):
            return grp
    raise ValueError(f"Cannot determine group for control target '{target}'")


def _load_group_totals(pipeline):
    """Load group->decennial-total mapping with overrides applied."""
    configs_root = Path(str(resources.files('mazpop.configs')))
    df = pd.read_csv(configs_root / 'group_totals.csv')
    mapping = dict(zip(df['group'].astype(str), df['total'].astype(str)))
    mapping.update(GROUP_TOTAL_OVERRIDES)
    return mapping


def _load_special_control_totals(pipeline):
    """Build {control_target: totals_column} for standalone (non-group) controls.

    Totals are read from ``total_variables.csv`` in the ACS and decennial config
    dirs. Each total name maps to itself; an optional ``control_alias`` column
    adds extra control names (e.g. ``num_hh`` -> ``hh``) that have no API
    variable of their own but should equal an existing total when used as a
    marginal control. This keeps the mapping in config rather than hardcoded.
    """
    base_year = pipeline.context['year']
    acs_year = pipeline.context['acs_year']
    mapping = {}
    for config_dir in (pipeline.get_acs_config_dir(acs_year), pipeline.get_dec_config_dir(base_year)):
        path = Path(config_dir) / 'total_variables.csv'
        if not path.exists():
            continue
        df = pd.read_csv(path, dtype=str).fillna('')
        for _, row in df.iterrows():
            name = str(row['name']).strip()
            if not name:
                continue
            mapping[name] = name
            alias = str(row.get('control_alias', '')).strip()
            if alias:
                mapping[alias] = name
    return mapping


def _build_block_lookup(pipeline):
    """Load puma_block_lookup.csv and ensure id columns are ints."""
    path = Path(pipeline.get_popsim_root_dir()) / 'data' / 'puma_block_lookup.csv'
    if not path.exists():
        raise FileNotFoundError(f"Required lookup not found: {path}")
    lookup = pd.read_csv(path)
    for col in ('block_id', 'tract_id', 'county_id', 'puma_id', 'region'):
        if col in lookup.columns:
            lookup[col] = lookup[col].astype(np.int64)
    return lookup


def _tract_to_county(lookup):
    """Return a Series mapping tract_id -> county_id from the lookup."""
    if 'county_id' not in lookup.columns:
        raise RuntimeError(
            "puma_block_lookup.csv is missing required 'county_id' column."
        )
    pairs = lookup[['tract_id', 'county_id']].drop_duplicates()
    return pairs.set_index('tract_id')['county_id']


def _aggregate_to_tract(block_df, lookup, id_col='geoid'):
    """Sum numeric columns of a block-level table up to tract via lookup."""
    merged = block_df.merge(
        lookup[['block_id', 'tract_id']],
        left_on=id_col,
        right_on='block_id',
        how='inner',
    )
    numeric = merged.select_dtypes(include='number').columns.tolist()
    numeric = [c for c in numeric if c not in ('block_id', 'tract_id', id_col)]
    return merged.groupby('tract_id', as_index=False)[numeric].sum()


def _saferound_rows(values_df, targets):
    """Round each row of values_df so it sums to the matching target.

    values_df: numeric DataFrame indexed by geography id.
    targets: Series indexed identically with per-row integer targets.
    Returns DataFrame of integers with same shape and index.
    """
    out = pd.DataFrame(0, index=values_df.index, columns=values_df.columns, dtype=np.int64)
    for idx, row in values_df.iterrows():
        target = targets.get(idx, 0)
        if pd.isna(target):
            target = 0
        target = int(round(float(target)))
        row_vals = row.fillna(0).astype(float).tolist()
        row_sum = sum(row_vals)
        if target <= 0 or row_sum <= 0:
            continue
        # Scale to target so saferound preserves the desired total.
        scaled = [v * target / row_sum for v in row_vals]
        rounded = saferound(scaled, 0)
        out.loc[idx] = [int(x) for x in rounded]
    return out


def _scale_group(group_df, group_cols, totals, id_col, county_fallback=None):
    """Convert group counts to proportions per row and rescale to per-row totals.

    group_df: DataFrame with id_col + group_cols (raw ACS counts at the geog).
    group_cols: list of category column names.
    totals: Series indexed by id_col values giving the per-row decennial total.
    county_fallback: optional tuple (tract_to_county_series, county_sums_df) where
        county_sums_df is indexed by county_id with group_cols columns. For any
        row whose group columns sum to 0 (no ACS data) but whose target total is
        > 0, the row is replaced with that tract's county aggregate before
        rescaling so the proportions come from the parent county.
    Returns DataFrame indexed by id_col with integer columns summing to totals.
    """
    df = group_df.set_index(id_col)[group_cols].astype(float)
    df = df.reindex(totals.index).fillna(0)

    if county_fallback is not None:
        tract_to_county, county_sums = county_fallback
        row_sums = df.sum(axis=1)
        for idx in df.index[row_sums == 0]:
            target = totals.get(idx, 0)
            if pd.isna(target) or float(target) <= 0:
                continue
            cid = tract_to_county.get(idx)
            if cid is None or cid not in county_sums.index:
                continue
            county_row = county_sums.loc[cid]
            if float(county_row.sum()) <= 0:
                continue
            df.loc[idx] = county_row.values

    return _saferound_rows(df, totals)


def _validate_controls(controls, group_to_columns, special_totals):
    """Strict-fail if any control's source group/column is missing."""
    missing = []
    for _, row in controls.iterrows():
        target = str(row['target'])
        if target in special_totals:
            continue
        group = _derive_group(target, special_totals)
        cols = group_to_columns.get(group, set())
        if target not in cols:
            missing.append(f"{target} (group={group})")
    if missing:
        raise RuntimeError(
            "Required control source columns not found: " + ", ".join(missing)
        )


def _read_group_table(pipeline, group):
    """Load a group table from the decennial or ACS pipeline output."""
    acs_year = pipeline.context['acs_year']
    base_year = pipeline.context['year']
    if group in _DEC_GROUPS:
        return pipeline.get_table(f'dec_data_{base_year}/{group}')
    return pipeline.get_table(f'acs_data_{acs_year}/{group}')


def _group_targets_in_df(df, controls, geography, group_total_map, special_totals):
    """Build {group: (total_col, [target_cols])} for the given geography."""
    geog_controls = controls[controls['geography'] == geography]
    grouped = {}
    for _, row in geog_controls.iterrows():
        target = str(row['target'])
        if target in special_totals:
            total_col = special_totals[target]
            grouped.setdefault(target, (total_col, []))[1].append(target)
            continue
        group = _derive_group(target, special_totals)
        total_col = group_total_map.get(group)
        if total_col is None:
            continue
        grouped.setdefault(group, (total_col, []))[1].append(target)
    return grouped


def _check_row_group_totals(out_df, controls, geography, group_total_map,
                             special_totals, region_totals=None, geog_label=None):
    """Verify each group's columns sum to the row's target total per row.

    For block/tract: target totals come from columns already present in out_df.
    For region: out_df is a single row and target totals come from
    `region_totals` (a dict).
    Raises RuntimeError listing every group/row mismatch.
    """
    label = geog_label or geography
    grouped = _group_targets_in_df(out_df, controls, geography, group_total_map, special_totals)
    if not grouped:
        return

    errors = []
    for group, (total_col, targets) in grouped.items():
        if group in special_totals:
            # Standalone single-column controls — verify equals the total.
            for t in targets:
                if region_totals is not None:
                    target_total = int(region_totals.get(total_col, 0))
                    actual = int(out_df.iloc[0][t])
                    if actual != target_total:
                        errors.append(
                            f"[{label}] {t} = {actual} but expected {total_col}={target_total}"
                        )
                else:
                    if total_col not in out_df.columns:
                        errors.append(
                            f"[{label}] missing total column '{total_col}' to validate '{t}'"
                        )
                        continue
                    diff = (out_df[t].astype(np.int64) - out_df[total_col].astype(np.int64))
                    bad = int((diff != 0).sum())
                    if bad:
                        errors.append(
                            f"[{label}] {t} != {total_col} in {bad} rows"
                        )
            continue

        if region_totals is not None:
            target_total = int(region_totals.get(total_col, 0))
            row_sum = int(out_df[targets].sum(axis=1).iloc[0])
            if row_sum != target_total:
                errors.append(
                    f"[{label}] group '{group}' sums to {row_sum} but expected "
                    f"{total_col}={target_total}"
                )
        else:
            if total_col not in out_df.columns:
                errors.append(
                    f"[{label}] missing total column '{total_col}' to validate group '{group}'"
                )
                continue
            row_sums = out_df[targets].sum(axis=1).astype(np.int64)
            target_vals = out_df[total_col].astype(np.int64)
            mismatch = row_sums != target_vals
            n_bad = int(mismatch.sum())
            if n_bad:
                # Show up to 3 sample bad rows.
                id_col = geography
                sample = out_df.loc[mismatch, [id_col]].head(3)
                sample_ids = sample[id_col].tolist()
                errors.append(
                    f"[{label}] group '{group}' rows where sum != {total_col}: "
                    f"{n_bad} (e.g. {id_col}={sample_ids})"
                )

    if errors:
        raise RuntimeError(
            "Per-row group total validation failed:\n  - "
            + "\n  - ".join(errors)
        )


def _check_cross_geog_totals(child_df, controls, geography, group_total_map,
                              special_totals, region_totals, geog_label=None):
    """Verify each group's columns summed across all child rows equal the
    region's mapped total (e.g. tract income_* sums to region hh).
    """
    label = geog_label or geography
    grouped = _group_targets_in_df(child_df, controls, geography, group_total_map, special_totals)
    if not grouped:
        return

    errors = []
    for group, (total_col, targets) in grouped.items():
        region_total = int(region_totals.get(total_col, 0))
        if group in special_totals:
            for t in targets:
                actual = int(child_df[t].sum())
                if actual != region_total:
                    errors.append(
                        f"[{label}->region] sum({t}) = {actual} but region "
                        f"{total_col} = {region_total}"
                    )
            continue
        actual = int(child_df[targets].to_numpy().sum())
        if actual != region_total:
            errors.append(
                f"[{label}->region] sum(group '{group}') = {actual} but region "
                f"{total_col} = {region_total}"
            )

    if errors:
        raise RuntimeError(
            "Cross-geography total validation failed:\n  - "
            + "\n  - ".join(errors)
        )


def build_block_marginals(pipeline, controls, lookup, dec_totals_block,
                          special_totals, total_cols):
    """Block-level marginals: direct decennial counts only."""
    block_controls = controls[controls['geography'] == 'block_id']

    blocks = lookup[['block_id']].drop_duplicates().sort_values('block_id').reset_index(drop=True)
    out = blocks.copy()

    # Always-present extra totals from dec_totals (for QA).
    extras = dec_totals_block.set_index('geoid')
    for col in total_cols:
        if col in extras.columns:
            out[col] = out['block_id'].map(extras[col]).fillna(0).astype(np.int64)

    group_tables = {}
    for _, row in block_controls.iterrows():
        target = str(row['target'])
        if target in special_totals:
            total_col = special_totals[target]
            if total_col not in out.columns:
                raise RuntimeError(
                    f"Decennial total '{total_col}' for control '{target}' is "
                    f"not available at block level."
                )
            out[target] = out[total_col]
            continue

        group = _derive_group(target, special_totals)
        if group not in _DEC_GROUPS:
            raise RuntimeError(
                f"Block-level control '{target}' belongs to group '{group}', "
                f"which has no decennial block data."
            )
        if group not in group_tables:
            df = _read_group_table(pipeline, group)
            df['geoid'] = df['geoid'].astype(np.int64)
            group_tables[group] = df.set_index('geoid')
        group_df = group_tables[group]
        if target not in group_df.columns:
            raise RuntimeError(f"Column '{target}' missing from dec_data_{pipeline.context['year']}/{group}")
        out[target] = out['block_id'].map(group_df[target]).fillna(0).astype(np.int64)

    return out


def build_tract_marginals(pipeline, controls, lookup, group_total_map,
                          dec_totals_tract, tenure_tract, special_totals,
                          total_cols):
    """Tract-level marginals: ACS proportions × tract decennial totals."""
    tract_controls = controls[controls['geography'] == 'tract_id']
    tract_to_county = _tract_to_county(lookup)
    tracts = lookup[['tract_id']].drop_duplicates().sort_values('tract_id').reset_index(drop=True)
    out = tracts.copy()

    # Build a per-tract totals frame combining decennial sums + tenure sums.
    totals = dec_totals_tract.set_index('tract_id')
    tenure = tenure_tract.set_index('tract_id')

    totals_lookup = pd.DataFrame(index=tracts['tract_id'].values)
    for col in total_cols:
        if col in totals.columns:
            totals_lookup[col] = totals[col]
    if 'tenure_owner' in tenure.columns:
        totals_lookup['owner_hh'] = tenure['tenure_owner']
    if 'tenure_renter' in tenure.columns:
        totals_lookup['renter_hh'] = tenure['tenure_renter']
    totals_lookup = totals_lookup.fillna(0)

    # Add extras to output for QA.
    for col in totals_lookup.columns:
        out[col] = out['tract_id'].map(totals_lookup[col]).fillna(0).astype(np.int64)

    # Group controls by group so we read each ACS table once.
    grouped = {}
    for _, row in tract_controls.iterrows():
        target = str(row['target'])
        group = _derive_group(target, special_totals)
        grouped.setdefault(group, []).append(target)

    for group, targets in grouped.items():
        total_name = group_total_map.get(group)
        if total_name is None:
            raise RuntimeError(f"No decennial total mapping for group '{group}'")
        if total_name not in totals_lookup.columns:
            raise RuntimeError(
                f"Decennial total '{total_name}' for group '{group}' is not "
                f"available at tract level."
            )

        group_df = _read_group_table(pipeline, group)
        for t in targets:
            if t not in group_df.columns:
                raise RuntimeError(f"Column '{t}' missing from acs_data_{pipeline.context['acs_year']}/{group}")

        # Restrict to project tracts and the columns we need.
        sub = group_df[['geoid'] + list(targets)].copy()
        sub = sub.rename(columns={'geoid': 'tract_id'})
        sub['tract_id'] = sub['tract_id'].astype(np.int64)
        sub = sub[sub['tract_id'].isin(out['tract_id'])]

        # County-level fallback for tracts where the ACS group sums to 0.
        sub_with_county = sub.merge(
            tract_to_county.rename('county_id'),
            left_on='tract_id', right_index=True, how='left',
        )
        county_sums = sub_with_county.groupby('county_id')[list(targets)].sum()

        target_series = totals_lookup[total_name].reindex(out['tract_id'].values).fillna(0)
        target_series.index = out['tract_id'].values

        scaled = _scale_group(
            sub, list(targets), target_series, 'tract_id',
            county_fallback=(tract_to_county, county_sums),
        )
        for t in targets:
            out[t] = out['tract_id'].map(scaled[t]).fillna(0).astype(np.int64)

    return out


def build_region_marginals(pipeline, controls, lookup, group_total_map,
                           dec_totals_block, tenure_block, acs_totals_tract,
                           special_totals, total_cols):
    """Region-level marginals: ACS region sum scaled to decennial region total."""
    region_controls = controls[controls['geography'] == 'region']

    region_id = int(lookup['region'].iloc[0]) if 'region' in lookup.columns else 1
    out = pd.DataFrame({'region': [region_id]})

    # Region totals: sum decennial blocks within project, plus ACS workers.
    project_blocks = set(lookup['block_id'].astype(np.int64).tolist())
    dec_in = dec_totals_block[dec_totals_block['geoid'].astype(np.int64).isin(project_blocks)]
    region_totals = {}
    for col in total_cols:
        if col in dec_in.columns:
            region_totals[col] = int(dec_in[col].sum())

    tenure_in = tenure_block[tenure_block['geoid'].astype(np.int64).isin(project_blocks)]
    if 'tenure_owner' in tenure_in.columns:
        region_totals['owner_hh'] = int(tenure_in['tenure_owner'].sum())
    if 'tenure_renter' in tenure_in.columns:
        region_totals['renter_hh'] = int(tenure_in['tenure_renter'].sum())

    project_tracts = set(lookup['tract_id'].astype(np.int64).tolist())
    acs_in = acs_totals_tract[acs_totals_tract['geoid'].astype(np.int64).isin(project_tracts)]
    if 'workers' in acs_in.columns:
        region_totals['workers'] = int(acs_in['workers'].sum())

    # Add extras to output for QA.
    for col, val in region_totals.items():
        out[col] = val

    # Group controls by group.
    grouped = {}
    for _, row in region_controls.iterrows():
        target = str(row['target'])
        if target in special_totals:
            grouped.setdefault('__special__', []).append(target)
            continue
        group = _derive_group(target, special_totals)
        grouped.setdefault(group, []).append(target)

    for group, targets in grouped.items():
        if group == '__special__':
            for t in targets:
                total_col = special_totals.get(t)
                if total_col is None:
                    raise RuntimeError(f"Unsupported special region control '{t}'")
                out[t] = region_totals.get(total_col, 0)
            continue

        total_name = group_total_map.get(group)
        if total_name is None:
            raise RuntimeError(f"No decennial total mapping for group '{group}'")
        if total_name not in region_totals:
            raise RuntimeError(
                f"Region total '{total_name}' for group '{group}' is not available."
            )
        region_total = int(region_totals[total_name])

        group_df = _read_group_table(pipeline, group)
        for t in targets:
            if t not in group_df.columns:
                raise RuntimeError(f"Column '{t}' missing from acs_data_{pipeline.context['acs_year']}/{group}")

        # Sum tract-level group counts across project tracts.
        sub = group_df[group_df['geoid'].astype(np.int64).isin(project_tracts)]
        region_sum = sub[list(targets)].sum().astype(float)
        row_sum = float(region_sum.sum())

        if region_total <= 0 or row_sum <= 0:
            for t in targets:
                out[t] = 0
            continue

        scaled = (region_sum * region_total / row_sum).tolist()
        rounded = saferound(scaled, 0)
        for t, v in zip(targets, rounded):
            out[t] = int(v)

    return out, region_totals


def run_step(context):
    pipeline = Pipeline(context)
    popsim_root_dir = pipeline.get_popsim_root_dir()
    base_year = pipeline.context['year']
    acs_year = pipeline.context['acs_year']
    # Load project controls.
    controls_path = popsim_root_dir / 'configs' / 'controls.csv'
    if not controls_path.exists():
        raise FileNotFoundError(f"controls.csv not found: {controls_path}")
    controls = pd.read_csv(controls_path)
    controls['target'] = controls['target'].astype(str)
    controls['geography'] = controls['geography'].astype(str)

    # Mappings & lookups.
    _load_known_groups(pipeline)
    group_total_map = _load_group_totals(pipeline)
    special_totals = _load_special_control_totals(pipeline)
    total_cols = _load_dec_total_columns(pipeline)
    lookup = _build_block_lookup(pipeline)

    # Source tables.
    dec_totals_block = pipeline.get_table(f'dec_data_{base_year}/dec_totals')
    dec_totals_block['geoid'] = dec_totals_block['geoid'].astype(np.int64)
    tenure_block = pipeline.get_table(f'dec_data_{base_year}/tenure')
    tenure_block['geoid'] = tenure_block['geoid'].astype(np.int64)
    acs_totals_tract = pipeline.get_table(f'acs_data_{acs_year}/acs_totals')
    acs_totals_tract['geoid'] = acs_totals_tract['geoid'].astype(np.int64)

    # Validate controls have matching source columns up front (strict mode).
    print('Validating controls against source data')
    group_to_columns = {}
    for group in {_derive_group(t, special_totals) for t in controls['target'] if t not in special_totals}:
        df = _read_group_table(pipeline, group)
        group_to_columns[group] = set(df.columns)
    _validate_controls(controls, group_to_columns, special_totals)

    # Aggregations.
    print('Aggregating decennial block totals to tract')
    dec_totals_tract = _aggregate_to_tract(dec_totals_block, lookup, id_col='geoid')
    tenure_tract = _aggregate_to_tract(tenure_block, lookup, id_col='geoid')

    # Build outputs.
    print('Building block marginals')
    block_marginals = build_block_marginals(
        pipeline, controls, lookup, dec_totals_block, special_totals, total_cols,
    )

    print('Building tract marginals')
    tract_marginals = build_tract_marginals(
        pipeline, controls, lookup, group_total_map,
        dec_totals_tract, tenure_tract, special_totals, total_cols,
    )

    print('Building region marginals')
    region_marginals, region_totals = build_region_marginals(
        pipeline, controls, lookup, group_total_map,
        dec_totals_block, tenure_block, acs_totals_tract, special_totals,
        total_cols,
    )

    # Validate per-row group totals at every geography.
    print('Validating per-row group totals')
    _check_row_group_totals(
        block_marginals, controls, 'block_id', group_total_map,
        special_totals, geog_label='block',
    )
    _check_row_group_totals(
        tract_marginals, controls, 'tract_id', group_total_map,
        special_totals, geog_label='tract',
    )
    _check_row_group_totals(
        region_marginals, controls, 'region', group_total_map,
        special_totals, region_totals=region_totals, geog_label='region',
    )

    # Validate cross-geography roll-ups (block->region, tract->region).
    # Region marginals themselves are not checked against a parent.
    print('Validating cross-geography roll-ups')
    _check_cross_geog_totals(
        block_marginals, controls, 'block_id', group_total_map,
        special_totals, region_totals, geog_label='block',
    )
    _check_cross_geog_totals(
        tract_marginals, controls, 'tract_id', group_total_map,
        special_totals, region_totals, geog_label='tract',
    )

    # Write to project data dir.
    data_dir = popsim_root_dir / 'data'
    block_path = data_dir / 'block_marginals.csv'
    tract_path = data_dir / 'tract_marginals.csv'
    region_path = data_dir / 'region_marginals.csv'

    print(f'Writing {block_path}')
    block_marginals.to_csv(block_path, index=False)
    print(f'Writing {tract_path}')
    tract_marginals.to_csv(tract_path, index=False)
    print(f'Writing {region_path}')
    region_marginals.to_csv(region_path, index=False)
