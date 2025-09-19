import os
import shutil
from typing import Optional
import aio_pika
from fastapi import FastAPI, Form, UploadFile, HTTPException, Request
from fastapi.staticfiles import StaticFiles
import json
import uuid
import pypdfium2
import glob
import psutil
import asyncio
from pydantic import BaseModel

from inference.server.chunking import maybe_chunk_pdf
from inference.server.merge import (
    _get_image_files,
    _merge_chunk_files,
    _extract_worker_info,
)
from inference.server.files import (
    get_output_path,
    get_file_path,
    get_potential_file_paths,
    OUTPUT_DIR,
    DATA_DIR,
)

JOB_TYPES = [
    "marker",
]

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 32))
RABBIT_MQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")

connection = None
channel = None


async def setup_rabbitmq_connection():
    """Set up an async connection and channel to RabbitMQ with retry logic."""
    global connection, channel
    max_retries = 10
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            # Async connection to RabbitMQ
            connection = await aio_pika.connect_robust(f"amqp://{RABBIT_MQ_HOST}")
            channel = await connection.channel()

            # Declare the queue
            for job_type in JOB_TYPES:
                await channel.declare_queue(f"{job_type}_queue", durable=True)

            print("RabbitMQ connection and channel set up successfully.")
            return
        except Exception as e:
            print(
                f"Failed to connect to RabbitMQ (attempt {attempt + 1}/{max_retries}): {e}"
            )
            if attempt < max_retries - 1:
                print(f"Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 1.5, 10)  # Exponential backoff, max 10s
            else:
                print("Max retries exceeded. RabbitMQ connection failed.")
                raise HTTPException(
                    status_code=500,
                    detail=f"RabbitMQ connection failed after {max_retries} attempts: {str(e)}",
                )


async def lifespan(app: FastAPI):
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    await setup_rabbitmq_connection()
    yield

    # Clean up connection on shutdown
    global connection, channel
    if channel is not None:
        await channel.close()

    if connection is not None:
        await connection.close()

    del connection
    del channel


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=OUTPUT_DIR), name="static")


@app.get("/health_check")
async def health_check():
    """Health check endpoint to verify the server is running."""
    return {"status": "healthy"}


@app.get("/status")
async def status():
    # Try to count actual worker processes
    worker_processes = 0
    try:
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(proc.info["cmdline"] or [])
                if "inference/worker/main.py" in cmdline:
                    worker_processes += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass

    return {
        "status": "running",
        "num_workers_running": worker_processes,
        "rabbitmq_host": RABBIT_MQ_HOST,
        "chunk_size": CHUNK_SIZE,
        "data_dir": DATA_DIR,
        "output_dir": OUTPUT_DIR,
    }


@app.get("/marker/results")
async def marker_results(request: Request, file_id: str, download: bool = False):
    """Returns the status or results of a marker job by file_id.

    Query Parameters:
    - file_id (str): ID of the job to retrieve.
    - download (bool): If True, returns merged result and associated images.
    """
    output_path = get_output_path(file_id)

    # Check if job directory exists
    if not os.path.exists(output_path):
        return {"file_id": file_id, "status": "processing"}

    # Check for error file
    error_file = os.path.join(output_path, "ERROR")
    if os.path.exists(error_file):
        with open(error_file, "r") as f:
            return {"file_id": file_id, "status": "failed", "error": f.read()}

    # Check for cached merged file
    merged_files = glob.glob(os.path.join(output_path, "merged.*"))
    if merged_files:
        if not download:
            return {"file_id": file_id, "status": "done"}

        # Return cached merged result with images
        with open(merged_files[0], "r") as f:
            merged_result = f.read()

        response = {"file_id": file_id, "status": "done", "result": merged_result}
        response["images"] = _get_image_files(request, output_path, file_id)
        response["worker_info"] = _extract_worker_info(output_path)
        return response

    # Try to merge chunk files
    merged_result, ext = _merge_chunk_files(output_path)

    if merged_result is None:
        return {"file_id": file_id, "status": "processing"}

    # Job is complete - prepare response
    response = {"file_id": file_id, "status": "done"}

    if download:
        response["worker_info"] = _extract_worker_info(output_path)
        response["result"] = merged_result
        response["images"] = _get_image_files(request, output_path, file_id)

    return response


@app.post("/marker/inference")
async def marker_inference(file: UploadFile, config: Optional[str] = Form("{}")):
    """Handles PDF file uploads, validates input, and queues inference jobs.

    Form Data:
    - file (UploadFile): The PDF file to be processed.
    - config (str): Optional JSON string with the marker configuration
    """

    file_id = str(uuid.uuid4())
    file_path, filename = get_file_path(file_id, file.filename)

    # Catch bad configs here, don't waste worker resources
    try:
        config_dict = json.loads(config)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        doc = pypdfium2.PdfDocument(file_path)
        page_count = len(doc)
        doc.close()
    except Exception as e:
        os.remove(file_path)
        raise HTTPException(status_code=400, detail=f"Invalid PDF file - {e}")

    requests = maybe_chunk_pdf(file_id, filename, config_dict, page_count, CHUNK_SIZE)

    for request in requests:
        try:
            if any(
                [
                    connection is None,
                    channel is None,
                    getattr(connection, "is_closed", True),
                    getattr(channel, "is_closed", True),
                ]
            ):
                await setup_rabbitmq_connection()

            if connection is None or channel is None:
                raise HTTPException(
                    status_code=500, detail="Failed to establish RabbitMQ connection"
                )

            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(request).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key="marker_queue",
            )
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Internal server error: {str(e)}"
            )

    return {"file_id": file_id}


class ClearRequest(BaseModel):
    file_id: str


@app.post("/marker/clear")
async def marker_clear(request_data: ClearRequest):
    file_id = request_data.file_id
    output_path = get_output_path(file_id)
    if os.path.exists(output_path):
        shutil.rmtree(output_path)

    data_paths = get_potential_file_paths(file_id)
    for data_path in data_paths:
        try:
            os.remove(data_path)
        except Exception as e:
            print(f"Failed to remove {data_path}: {e}")

    return {"file_id": file_id, "status": "cleared"}
