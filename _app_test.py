"""Temporary AppTest verification of the editor GUI (delete after use).

Runs the real app headlessly, switches the synthesis year, and asserts that the
Marginals Groups and Controls tabs reload that year's configs.
"""

import pandas as pd
from streamlit.testing.v1 import AppTest

BASE_YAML = "base_marginals_groups.yaml"
HIST_YAML = "history_marginals_groups.yaml"
BASE_CONTROLS = "popsim_base_year/configs/controls.csv"
HIST_CONTROLS = "popsim_history_year/configs/controls.csv"

at = AppTest.from_file("src/mazpop/gui/_launch.py", default_timeout=180)
at.run()

print("app exceptions:", [e.value for e in at.exception])
print("title:", [t.value for t in at.title])
print("tabs:", [t.label for t in at.tabs])

year_radio = next(r for r in at.radio if r.key == "year_choice")
print("year options:", year_radio.options, "value:", year_radio.value)


def report(stage: str):
    text = " ".join(t.value for t in at.caption) + " " + " ".join(
        t.value for t in at.markdown
    )
    codes = [c.value for c in at.code if "hhpop_age" in c.value]
    frames = [
        d.value
        for d in at.dataframe
        if isinstance(d.value, pd.DataFrame) and "target" in getattr(d.value, "columns", [])
    ]
    print(f"\n=== {stage} ===")
    print("  exceptions:", [e.value for e in at.exception])
    print(f"  sidebar mentions {BASE_YAML}:", BASE_YAML in text)
    print(f"  sidebar mentions {HIST_YAML}:", HIST_YAML in text)
    print(f"  mentions {BASE_CONTROLS}:", BASE_CONTROLS in text)
    print(f"  mentions {HIST_CONTROLS}:", HIST_CONTROLS in text)
    print("  yaml previews found:", len(codes))
    print("  base-only bin '1500k_2000k' in preview:", any("1500k_2000k" in c for c in codes))
    print("  history-only bin '2500_plus' in preview:", any("2500_plus" in c for c in codes))
    if frames:
        df = frames[0]
        targets = set(df["target"])
        geo = df.groupby("geography")["target"].count().to_dict()
        print(f"  controls preview rows: {len(df)}  geographies: {geo}")
        print("  has income_125k_200k:", "income_125k_200k" in targets)
        print("  has stale income_125k_plus:", "income_125k_plus" in targets)
        print("  has tenure_owner:", "tenure_owner" in targets)
    else:
        print("  no controls preview dataframe found")


report("BASE (2020)")

year_radio.set_value("History year (2010)").run()
report("HISTORY (2010)")

year_radio = next(r for r in at.radio if r.key == "year_choice")
year_radio.set_value("Base year (2020)").run()
report("BASE again")
