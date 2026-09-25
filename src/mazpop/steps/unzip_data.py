from pathlib import Path

from mazpop.util.pipeline import Pipeline
import zipfile

def run_step(context):
    pipeline = Pipeline(context)
    zip_files = list(Path(pipeline.data_dir).glob("*.zip"))
    for zip_file in zip_files:
        print(f"Unzipping {zip_file}")
        with zipfile.ZipFile(zip_file, 'r') as zip_ref:
            zip_ref.extractall(pipeline.data_dir)
    return context