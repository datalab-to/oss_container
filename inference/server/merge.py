import glob
import os
from typing import List
import json
from bs4 import BeautifulSoup
from copy import deepcopy
from fastapi import Request


def merge_json(results: List[str]):
    full_output = json.loads(results[0])
    if isinstance(full_output, str):
        full_output = json.loads(full_output)

    for res in results[1:]:
        res_json = json.loads(res)
        children = res_json["children"]
        full_output["children"].extend(children)

    full_output = json.dumps(full_output)
    return full_output


def merge_markdown(results: List[str]):
    full_markdown = results[0]
    print("Results length", len(results))

    for res in results[1:]:
        full_markdown += "\n" + res
    return full_markdown


def merge_html(results: List[str]):
    full_html = results[0]
    soup = BeautifulSoup(full_html, "html.parser")
    body = soup.body
    print("Results length", len(results))

    for res in results[1:]:
        res_body = BeautifulSoup(res, "html.parser").body
        for tag in res_body.contents:
            body.append(deepcopy(tag))
    return str(soup)


def merge_marker_results(results: List[str], ext=str):
    match ext:
        case ".md":
            return merge_markdown(results)
        case ".html":
            return merge_html(results)
        case ".json":
            return merge_json(results)
        case _:
            raise NotImplementedError(f"Unrecognized result type with extension {ext}")


def _get_image_files(request: Request, output_path: str, file_id: str):
    """Helper function to get image file URLs."""
    return [
        f"{request.base_url}static/{file_id}/{os.path.basename(f)}"
        for f in glob.glob(os.path.join(output_path, "*"))
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".gif"))
    ]


def _extract_worker_info(output_path: str):
    worker_files = [
        fname
        for fname in glob.glob(os.path.join(output_path, "*_worker_info.json"))
        if "meta.json" not in fname
    ]

    if not worker_files:
        return {}

    total_pages = 0
    total_time = 0
    for fname in worker_files:
        with open(os.path.join(output_path, fname), "r") as f:
            worker_info = json.load(f)

        total_pages += worker_info["pages"]
        total_time += worker_info["total_time"]

    return {"pages": total_pages, "worker_time": total_time}


def _merge_chunk_files(output_path: str):
    """Helper function to merge chunk files and return the merged result."""
    output_files = [
        fname
        for fname in glob.glob(os.path.join(output_path, "*-of-*.*"))
        if "meta.json" not in fname
    ]

    if not output_files:
        return None, None

    fname, ext = os.path.splitext(output_files[0])
    num_chunks = int(fname.split("-of-")[1])

    if len(output_files) < num_chunks:
        return None, None

    # Read and merge all chunk files
    results = []
    for file in sorted(output_files):
        with open(file, "r") as f:
            results.append(f.read())

    merged_result = merge_marker_results(results, ext)

    # Cache the merged result
    merged_file_path = os.path.join(output_path, f"merged{ext}")
    with open(merged_file_path, "w") as f:
        f.write(merged_result)

    return merged_result, ext
