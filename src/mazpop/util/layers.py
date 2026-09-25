"""Helpers to make sure configured layer files are available locally.

Spatial layers configured in ``settings.yaml`` (``spatial_layers`` and
``point_of_interest_layers``) are often distributed as zipped shapefiles. These
helpers check that each configured file exists in the project's data directory;
if any are missing, every ``.zip`` in the data directory is extracted and the
check is repeated, raising a clear error listing any files that are still
missing afterwards.
"""

import zipfile
from pathlib import Path


# Archives already extracted (or attempted) in this process, keyed by resolved
# path, so later steps don't extract the same archive again.
_EXTRACTED_ZIPS = set()


def unzip_archives(data_dir):
    """Extract every ``.zip`` in ``data_dir`` and return the paths that failed."""
    failed = []
    for zip_path in sorted(Path(data_dir).glob('*.zip')):
        zip_path = zip_path.resolve()
        if zip_path in _EXTRACTED_ZIPS:
            continue
        _EXTRACTED_ZIPS.add(zip_path)
        print(f"Unzipping {zip_path}")
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(data_dir)
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            print(f"Could not unzip {zip_path}: {exc}")
            failed.append(zip_path)
    return failed


def ensure_layers_exist(data_dir, layers, layer_kind='layer'):
    """Check that every configured layer file exists in ``data_dir``.

    ``layers`` is a settings list of dicts with a ``filename`` key. Missing
    files are looked for inside ``.zip`` archives in the data directory.
    Raises ``FileNotFoundError`` when any file is still missing afterwards.
    """
    missing = [
        layer['filename']
        for layer in layers
        if not (Path(data_dir) / layer['filename']).exists()
    ]
    if not missing:
        return

    print(f"Missing {layer_kind} file(s): {', '.join(missing)}. Attempting to unzip...")
    failed_zips = unzip_archives(data_dir)

    still_missing = [name for name in missing if not (Path(data_dir) / name).exists()]
    found = [name for name in missing if name not in still_missing]
    if found:
        print(f"Unzipping provided missing {layer_kind} file(s): {', '.join(found)}")
    if not still_missing:
        return

    message = (
        f"Missing {layer_kind} file(s) in the data directory ({data_dir}): "
        f"{', '.join(still_missing)}. Unzipping any .zip archives in the data "
        "directory did not provide them, so the file(s) are truly missing - "
        "add the file(s) (or a zip containing them) to the data directory."
    )
    if failed_zips:
        message += f" (could not unzip: {', '.join(str(p) for p in failed_zips)})"
    raise FileNotFoundError(message)
