#!/bin/bash

set -e

# General environment variables
export OPENBLAS_NUM_THREADS=${OPENBLAS_NUM_THREADS:-2}
export MKL_NUM_THREADS=${MKL_NUM_THREADS:-2}
export OMP_NUM_THREADS=${OMP_NUM_THREADS:-2}
export TORCH_NUM_THREADS=${TORCH_NUM_THREADS:-2}
export MKL_DYNAMIC=${MKL_DYNAMIC:-0}
export OMP_DYNAMIC=${OMP_DYNAMIC:-0}

# Marker/surya env vars
export CHUNK_SIZE=${DATALAB_INFERENCE_CHUNK_SIZE:-36}
export COMPILE_MODELS=${DATALAB_COMPILE_MODELS:-0}
export RECOGNITION_BATCH_SIZE=${DATALAB_INFERENCE_RECOGNITION_BATCH_SIZE:-48}
export DETECTION_BATCH_SIZE=${DATALAB_INFERENCE_DETECTION_BATCH_SIZE:-8}
export TABLE_REC_BATCH_SIZE=${DATALAB_INFERENCE_TABLE_REC_BATCH_SIZE:-12}
export LAYOUT_BATCH_SIZE=${DATALAB_INFERENCE_LAYOUT_BATCH_SIZE:-12}
export OCR_ERROR_BATCH_SIZE=${DATALAB_INFERENCE_OCR_ERROR_BATCH_SIZE:-12}
export DETECTOR_POSTPROCESSING_CPU_WORKERS=${DATALAB_DETECTOR_POSTPROCESSING_CPU_WORKERS:-2}

# Inference service environment variables
export DATALAB_INFERENCE_PORT=${DATALAB_INFERENCE_PORT:-8000}

# Used in this script
DATALAB_VRAM_PER_WORKER=${DATALAB_VRAM_PER_WORKER:-7}

# Function to get VRAM in GB for GPU 0
get_gpu_vram() {
    if command -v nvidia-smi &> /dev/null; then
        nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits -i 0 2>/dev/null | awk '{print int($1/1024)}' || echo "8"
    else
        echo "8"
    fi
}

# Function to start MPS
start_mps() {
    if command -v nvidia-smi &> /dev/null && nvidia-smi -L &>/dev/null; then
        echo "Starting NVIDIA MPS server..."
        export CUDA_VISIBLE_DEVICES=0
        export CUDA_MPS_PIPE_DIRECTORY=/tmp/nvidia-mps
        export CUDA_MPS_LOG_DIRECTORY=/tmp/nvidia-log
        mkdir -p $CUDA_MPS_PIPE_DIRECTORY $CUDA_MPS_LOG_DIRECTORY
        nvidia-cuda-mps-control -d 2>/dev/null && echo "✓ MPS server started"
    else
        echo "No GPU detected, skipping MPS"
    fi
}

# Function to stop MPS
stop_mps() {
    if pgrep -f "nvidia-cuda-mps" > /dev/null; then
        echo "Stopping MPS server..."
        echo quit | nvidia-cuda-mps-control 2>/dev/null || true
        nvidia-smi -r 2>/dev/null || true
        echo "✓ MPS stopped and GPU reset"
    fi
}


# Function to cleanup
cleanup() {
    echo "Shutting down..."
    
    # Stop supervisord (which will stop all managed processes)
    supervisorctl shutdown 2>/dev/null || true
    
    # Stop MPS
    stop_mps
    
    echo "✓ Cleanup complete"
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

echo "Starting inference server with GPU workers..."

# Start MPS first
start_mps

# Create log directory
mkdir -p /var/log

# Detect VRAM and calculate workers
vram_gb=$(get_gpu_vram)
num_workers=$(( vram_gb / DATALAB_VRAM_PER_WORKER ))
[ $num_workers -lt 1 ] && num_workers=1

echo "GPU VRAM: ${vram_gb}GB → spawning $num_workers workers"

# Export number of workers for supervisord
export NUM_WORKERS=$num_workers

# Start supervisord
echo "Starting supervisord..."
supervisord -c /inference/supervisord.conf

echo "✓ All services started under supervisord"
echo "✓ System ready: RabbitMQ + FastAPI server + $num_workers workers"

# Monitor supervisord - if it exits, we exit too
while pgrep -f "supervisord" > /dev/null; do
    sleep 5
done

echo "Supervisord died, shutting down..."
cleanup