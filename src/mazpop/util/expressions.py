"""Evaluate project CSV files that assign pandas expressions to variables.

Steps keep their project specific pandas expressions in a
``configs/*.csv`` file so the step code stays generic. Each row assigns one
target (``expr_out``) from one pandas expression (``expression``), e.g.::

    expr_out,expression,notes
    units['unit_id'],"range(1, len(units) + 1)",sequential housing unit ids

Rows are evaluated in file order, so an expression can use every variable
defined by an earlier row. Targets can be a plain variable (``households``), a
new column (``units['unit_id']``), a masked assignment
(``units.loc[sf_mask, 'unit_type_id']``), or a helper value (masks, dicts).
"""

import numpy as np
import pandas as pd


def evaluate_expression_csv(expressions_path, **tables):
    """Evaluate an expression CSV in file order and return the resulting variables.

    ``expressions_path`` is the path to a CSV with ``expr_out`` and
    ``expression`` columns. ``tables`` are the starting variables the
    expressions can reference (e.g. ``units``, ``persons``, ``blocks``), the
    same way helper functions are injected below.
    """
    expressions = pd.read_csv(expressions_path)

    # one shared scope dict is used as both globals and locals so that lambdas
    # and comprehensions in the expressions can reference the helper functions,
    # the starting tables, and any variable defined by an earlier row
    scope = {
        '__builtins__': {},
        'np': np,
        'pd': pd,
        'round': round,
        'str': str,
        'int': int,
        'float': float,
        'range': range,
        'len': len,
        'list': list,
    }
    scope.update(tables)

    for _, row in expressions.iterrows():
        target = str(row['expr_out']).strip()
        expr = str(row['expression']).strip()

        result = eval(expr, scope)  # noqa: S307
        # exec the assignment so that column and masked targets
        # (units['col'], units.loc[mask, 'col']) work like plain variables
        scope['__result__'] = result
        exec(f'{target} = __result__', scope)  # noqa: S102
        del scope['__result__']

    return scope
