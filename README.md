# Datalab Inference Service

Containerized inference service for [marker](https://github.com/datalab-to/marker).  This is not production-ready, and is only for evaluation purposes.  To get our production-ready container, see [here](https://www.datalab.to).

# Setup

This will run a single container on a single GPU, and will run enough parallel marker workers to saturate the GPU.

```bash
export IMAGE_TAG=datalab/marker:latest
docker build -t $IMAGE_TAG .
docker run --gpus device=0 -p 8000:8000 $IMAGE_TAG # Container can only handle one GPU
```

# Recommended Configurations
Here are a few recommended configurations that have been tested on a few different GPUs, to help set the number of workers and batch sizes
- **1xH100 GPU 80GB** (30 CPUs and 200GB RAM)
```
10 PDFs; 840 pages   ->    29.42s (28.552 pages/s)     

with `force_ocr` enabled
10 PDFs; 840 pages   ->    109.42s (9.31 pages/s)
```

# API Description and Endpoints

## `GET /health_check`

**Description:**  
Check if the service is up and running.

**Response:**  
```json
{ "status": "healthy" }
```

**Python Example:**
```python
import requests

res = requests.get("http://localhost:8000/health_check")
print(res.json())
```

---

## `POST /marker/inference`

**Description:**  
Upload a PDF and queue it for processing.

**Form Data:**

- `file` (UploadFile, required): The PDF file to process.
- `config` (str, optional): A JSON string containing configuration options.  Recommended options are:
  - `force_ocr` (bool): If `true`, runs OCR on all pages, even if text is detected.  Useful for scanned documents.
  - `drop_repeated_text` (bool): If `true`, drops text when OCR model degenerates (very rare).
  - `drop_repeated_table_text` (bool): If `true`, drops table text when OCR model degenerates (very rare).

**Response:**
```json
{ "file_id": "<file_id>" }
```

**Python Example:**
```python
import requests

files = {'file': open('example.pdf', 'rb')}
data = {'config': '{"force_ocr": true, "drop_repeated_text": true, "drop_repeated_table_text": true}'}
res = requests.post("http://localhost:8000/marker/inference", files=files, data=data)
print(res.json())
```

---

## `GET /marker/results`

**Description:**  
Check the status of a file or download the results once processing is done.

**Query Parameters:**

- `file_id` (str, required): The ID returned from the `/marker/inference` endpoint.
- `download` (bool, optional): If `true`, returns merged output and image URLs.

**Response (examples):**

**If processing is still ongoing:**
```json
{ "file_id": "<file_id>", "status": "processing" }
```

**If failed:**
```json
{ "file_id": "<file_id>", "status": "failed", "error": "Reason for failure" }
```

**If done:**
```json
{
  "file_id": "<file_id>",
  "status": "done",
  "result": "...",
  "images": ["https://.../image1.png", "..."]
}
```

Images will need to be fetched separately.

**Python Example:**
```python
import requests

params = {"file_id": "your-file-id", "download": True}
res = requests.get("http://localhost:8000/marker/results", params=params)
print(res.json())
```

## `POST /marker/clear`

**Description:**
Clear the results and data of a file that has been processed (freeing up disk space).

**Data:**
- `file_id` (str, required): The ID of the file to clear.

**Response:**
```json
{ "status": "cleared", "file_id": "<file_id>" }
```

**Python Example:**
```python
import requests
data = {'file_id': 'your-file-id'}
res = requests.post("http://localhost:8000/marker/clear", json=data)
print(res.json())
```