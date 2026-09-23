from mazpop.util import Pipeline


def run_step(context):
    p = Pipeline(context)
    year_key = context['year_key'] + '_year'
    acs_year_key = context['year_key'] + '_acs_year'
    context['year'] = p.settings[year_key]
    context['acs_year'] = p.settings[acs_year_key]
    print(f"Year set to: {context['year']}, ACS year set to: {context['acs_year']}")
    return context