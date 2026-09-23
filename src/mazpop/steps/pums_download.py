import us
import os
from zipfile import ZipFile
import io
from urllib.request import urlopen

from mazpop.util.pipeline import Pipeline


def get_data(census_year, pums_table, state_id_str, state_abbr, pipeline, overwrite=True):
    """
    Downloads PUMS data from the Census FTP and saves it as a CSV file in the data directory.
    If the file already exists, it will not download unless 'overwrite' is set to True.

    Args:
        census_year (int): Year of the PUMS data to download.
        pums_table (str): 'h' for household table, 'p' for person table.
        state_id_str (str): State FIPS code as a string.
        state_abbr (str): State abbreviation in lowercase.
        overwrite (bool): If True, delete existing file and download new one.
    """
    output_dir = pipeline.output_dir
    file_name = f"psam_{pums_table}{state_id_str}_{census_year}.csv"
    file_path = os.path.join(output_dir,"pipeline", file_name)

    if os.path.exists(file_path):
        if overwrite:
            os.remove(file_path)
            print(f"Deleted existing file: {file_path}")
        else:
            print(f"File already exists, skipping download: {file_path}")
            return

    pums_url = f"https://www2.census.gov/programs-surveys/acs/data/pums/{census_year}/5-Year/csv_{pums_table}{state_abbr}.zip"
    r = urlopen(pums_url).read()
    archive = ZipFile(io.BytesIO(r))
    # the CSV member's name varies by vintage (e.g. "psam_h53.csv" vs "ss11hwa.csv"),
    # so find it by extension instead of assuming the modern naming convention.
    csv_members = [name for name in archive.namelist() if name.lower().endswith('.csv')]
    if len(csv_members) != 1:
        raise ValueError(f"Expected exactly one CSV file in {pums_url}, found: {csv_members}")
    member_name = csv_members[0]
    pipeline_dir = os.path.join(output_dir, "pipeline")
    archive.extract(member_name, pipeline_dir)
    extracted_path = os.path.join(pipeline_dir, member_name)
    if extracted_path != file_path:
        os.replace(extracted_path, file_path)
    print(f"Downloaded and extracted: {file_name} to {pipeline_dir}")

def run_step(context):
    print("Downloading PUMS data...")
    pipeline = Pipeline(context)
    year = pipeline.context['acs_year']
    for state_id_str in pipeline.state_county_fips:
        state_abbr = us.states.mapping('fips', 'abbr')[state_id_str].lower()
        get_data(year,'h',state_id_str,state_abbr,pipeline,overwrite=True)
        get_data(year,'p',state_id_str,state_abbr,pipeline,overwrite=True)
    return context