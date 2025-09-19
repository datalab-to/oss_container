import json
import os
import pika
import time
import logging
import threading
import queue
import torch

from marker.models import create_model_dict
from marker.converters.pdf import PdfConverter
from marker.config.parser import ConfigParser
from marker.output import save_output
from surya.settings import settings as surya_settings

# Configuration
RABBIT_MQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
DATA_DIR = os.getenv("DATA_DIR", "/data")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output")
HEARTBEAT_WORKER_INTERVAL = int(os.getenv("HEARTBEAT_WORKER_INTERVAL", 10))
HEARTBEAT_TIMEOUT = int(os.getenv("HEARTBEAT_TIMEOUT", 120))
COMPILE_MODELS = bool(int(os.getenv("COMPILE_MODELS", 0)))

RECOGNITION_BATCH_SIZE = int(os.getenv("RECOGNITION_BATCH_SIZE", 64))
DETECTION_BATCH_SIZE = int(os.getenv("DETECTION_BATCH_SIZE", 8))
TABLE_REC_BATCH_SIZE = int(os.getenv("TABLE_REC_BATCH_SIZE", 12))
LAYOUT_BATCH_SIZE = int(os.getenv("LAYOUT_BATCH_SIZE", 12))
OCR_ERROR_BATCH_SIZE = int(os.getenv("OCR_ERROR_BATCH_SIZE", 12))
TORCH_NUM_THREADS = int(os.getenv("TORCH_NUM_THREADS", 2))

TASK_Q = queue.Queue(maxsize=50)  # messages → worker
RESULT_Q = queue.Queue()  # (delivery_tag, ok) → listener

# Setup logging
logging.basicConfig(level=logging.INFO)


def set_batch_sizes(config: dict):
    config["layout_batch_size"] = LAYOUT_BATCH_SIZE
    config["detection_batch_size"] = DETECTION_BATCH_SIZE
    config["table_rec_batch_size"] = TABLE_REC_BATCH_SIZE
    config["ocr_error_batch_size"] = OCR_ERROR_BATCH_SIZE
    config["recognition_batch_size"] = RECOGNITION_BATCH_SIZE
    config["equation_batch_size"] = max(
        2, RECOGNITION_BATCH_SIZE // 4
    )  # dynamically set equation batch size


def add_multiprocessing_config(config: dict):
    config["disable_multiprocessing"] = (
        True  # Disable multiprocessing to avoid high CPU usage
    )


def marker_inference(file_path, config, model_dict):
    add_multiprocessing_config(config)
    config_parser = ConfigParser(config)
    config_dict = config_parser.generate_config_dict()
    set_batch_sizes(config_dict)

    rendered = PdfConverter(
        config=config_dict,
        artifact_dict=model_dict,
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=config_parser.get_llm_service(),
    )(file_path)

    return rendered, config_dict


def run_marker_inference(
    message, marker_model_dict: dict, file_path: str, output_dir: str
):
    """Process a single message (the actual work)."""
    chunk_idx = message.get("chunk_idx")
    num_chunks = message.get("num_chunks")
    config = message.get("config")
    page_range = config.get("page_range", "")
    page_count = max(1, len(page_range.split(",")))

    os.makedirs(output_dir, exist_ok=True)

    config["filepath"] = file_path
    if "output_format" not in config:
        config["output_format"] = "markdown"

    start_time = time.time()
    rendered, config_dict = marker_inference(file_path, config, marker_model_dict)
    end_time = time.time()

    output_name = f"{chunk_idx:05}-of-{num_chunks:05}"
    save_output(rendered, output_dir, output_name)

    # Write worker-specific info
    meta_name = f"{chunk_idx}_worker_info.json"
    worker_info = {
        "start_time": start_time,
        "end_time": end_time,
        "total_time": end_time - start_time,
        "pages": page_count,
    }
    with open(os.path.join(output_dir, meta_name), "w") as f:
        json.dump(worker_info, f)

    # Write config dict
    if chunk_idx == num_chunks - 1:
        config_dict.pop("page_range")
        with open(os.path.join(output_dir, "config.json"), "w") as f:
            f.write(json.dumps(config_dict))

    logging.info(
        f"Completed processing: {file_path} -------- Chunk {chunk_idx} of {num_chunks}"
    )


def rabbit_listener():
    """RabbitMQ listener thread - handles connection and message routing."""
    while True:
        try:
            conn = pika.BlockingConnection(
                pika.ConnectionParameters(
                    host=RABBIT_MQ_HOST,
                    heartbeat=HEARTBEAT_TIMEOUT,
                    blocked_connection_timeout=300,
                    connection_attempts=3,
                    retry_delay=2,
                )
            )
            ch = conn.channel()
            ch.queue_declare(queue="marker_queue", durable=True)
            ch.basic_qos(prefetch_count=1)

            def on_msg(ch, method, props, body):
                try:
                    TASK_Q.put_nowait((method.delivery_tag, body))
                except queue.Full:
                    ch.basic_nack(method.delivery_tag, requeue=True)
                    logging.warning("Worker saturated; nacked message")

            ch.basic_consume("marker_queue", on_message_callback=on_msg)
            logging.info("RabbitMQ listener connected and waiting for messages")

            while True:  # main I/O loop
                conn.process_data_events(time_limit=1)  # pumps heartbeats
                time.sleep(0.5)
                try:
                    tag, ok = RESULT_Q.get_nowait()
                    try:
                        ch.basic_ack(tag)  # Ack the tag, even if the task failed
                    except pika.exceptions.AMQPError as e:
                        logging.error(f"Failed to ack/nack tag {tag}: {e}")

                except queue.Empty:
                    pass

        except Exception as e:
            logging.error(f"RabbitMQ listener error: {e}. Reconnecting in 5 seconds...")
            time.sleep(5)


def start_rabbit_thread():
    """Start the RabbitMQ listener thread."""
    t = threading.Thread(target=rabbit_listener, daemon=True)
    t.start()
    return t


def worker_loop(model_dict: dict):
    while True:
        tag, body = TASK_Q.get()
        try:
            msg = json.loads(body.decode())
        except Exception as e:
            logging.error(f"Failed to decode message body: {e}")
            RESULT_Q.put((tag, False))
            TASK_Q.task_done()
            continue

        file_id = msg.get("id")
        filename = msg.get("filename")

        file_path = os.path.join(DATA_DIR, filename)
        output_dir = os.path.join(OUTPUT_DIR, file_id)

        try:
            run_marker_inference(msg, model_dict, file_path, output_dir)
            RESULT_Q.put((tag, True))
        except Exception as e:
            os.makedirs(output_dir, exist_ok=True)
            with open(os.path.join(output_dir, "ERROR"), "w", encoding="utf-8") as f:
                f.write(f"Processing failed: {str(e)}")

            logging.exception("Failed to process message: %s", e)
            RESULT_Q.put((tag, False))
        finally:
            TASK_Q.task_done()  # always mark item done


def main():
    torch.set_num_threads(TORCH_NUM_THREADS)  # Set number of threads for PyTorch

    # Create marker model dictionary, force compilation
    if COMPILE_MODELS:
        surya_settings.COMPILE_ALL = True
        marker_model_dict = create_model_dict()

        # Run a single PDF for the intial compilation to run
        # Pages and config set so that all models are used
        logging.info("Running marker to compile models")
        marker_inference(
            "test.pdf",
            {
                "output_format": "markdown",
                "page_range": "0,5",
                "force_ocr": True,
            },
            marker_model_dict,
        )
    else:
        marker_model_dict = create_model_dict()

    # Start RabbitMQ listener thread
    start_rabbit_thread()

    # Run main worker loop (processes messages from task queue)
    worker_loop(marker_model_dict)


if __name__ == "__main__":
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    main()
