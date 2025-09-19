import os
from pathlib import Path

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output")
DATA_DIR = os.getenv("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)


def get_output_path(file_id: str):
    return os.path.join(OUTPUT_DIR, file_id)


def get_file_path(file_id: str, filename: str):
    filename = f"{file_id}{os.path.splitext(filename)[1]}"  # uuid.extension
    file_path = os.path.join(DATA_DIR, filename)
    return file_path, filename


def get_potential_file_paths(file_id: str):
    paths = Path(DATA_DIR).glob(f"{file_id}*")
    return [str(path) for path in paths if path.is_file()]
