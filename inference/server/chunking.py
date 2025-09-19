import math
from copy import deepcopy
from typing import List


def parse_range_str(range_str: str) -> List[int]:
    """
    Parse a range string into a 1D list - For example "1-3,5-9,18" -> [1,2,3,5,6,7,8,9,18]
    """
    range_lst = range_str.split(",")
    page_lst = []
    for i in range_lst:
        if "-" in i:
            start, end = i.split("-")
            page_lst += list(range(int(start), int(end) + 1))
        else:
            page_lst.append(int(i))
    page_lst = sorted(list(set(page_lst)))  # Deduplicate page numbers and sort in order
    return page_lst


def create_range_str(page_range: List[int]) -> str:
    return ",".join(map(str, page_range))


def maybe_chunk_pdf(
    file_id: str, filename: str, config: dict, page_count: int, chunk_size: int
) -> List[dict]:
    page_range = list(range(page_count))
    if "page_range" in config:
        page_range = parse_range_str(config["page_range"])

    if len(page_range) < 2 * chunk_size:
        config["page_range"] = create_range_str(page_range)
        return [
            {
                "id": file_id,
                "filename": filename,
                "config": config,
                "chunk_idx": 0,
                "num_chunks": 1,
            }
        ]

    num_chunks = math.ceil(len(page_range) / chunk_size)
    chunks = []
    for i in range(0, len(page_range), chunk_size):
        chunk_config = deepcopy(config)
        chunk_page_range = page_range[i : i + chunk_size]
        chunk_config["page_range"] = create_range_str(chunk_page_range)

        chunks.append(
            {
                "id": file_id,
                "filename": filename,
                "config": chunk_config,
                "chunk_idx": i // chunk_size,
                "num_chunks": num_chunks,
            }
        )

    return chunks
