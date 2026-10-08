#!/usr/bin/env bash
# Belongs on the DGX Spark as ~/vllm-qwen36-moe.sh, started by llama-swap.
# Qwen3.6-35B-A3B (mixture of experts, ~3B active per token) for coding
# agents. Mirrors ~/vllm-qwen38.sh: same image, same model cache, its own
# container name and a localhost-only port, so only llama-swap reaches it.
set -e

NAME="vllm-qwen36-moe"

stop_vllm() {
  docker stop "${NAME}" >/dev/null 2>&1 || true
}
trap stop_vllm EXIT INT TERM HUP

# Remove the container from an earlier session, if one exists.
docker rm -f "${NAME}" >/dev/null 2>&1 || true

docker run -d \
  --name "${NAME}" \
  --gpus all \
  --ipc host \
  --ulimit memlock=-1 \
  --ulimit stack=67108864 \
  --entrypoint "" \
  -p 127.0.0.1:11502:8000 \
  -v "$HOME/.cache/huggingface/hub:/root/.cache/huggingface/hub:ro" \
  -e HF_HUB_OFFLINE=1 \
  vllm/vllm-openai:qwen38 \
  vllm serve RedHatAI/Qwen3.6-35B-A3B-NVFP4 \
    --host 0.0.0.0 \
    --port 8000 \
    --tensor-parallel-size 1 \
    --gpu-memory-utilization 0.7 \
    --max-model-len 262144 \
    --max-num-seqs 8 \
    --max-num-batched-tokens 16384 \
    --kv-cache-dtype fp8_e4m3 \
    --enable-chunked-prefill \
    --async-scheduling \
    --enable-prefix-caching \
    --load-format fastsafetensors \
    --quantization compressed-tensors \
    --moe-backend flashinfer_cutlass \
    --language-model-only \
    --reasoning-parser qwen3 \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_coder \
    --enable-prompt-tokens-details \
    --speculative-config '{"method":"mtp","num_speculative_tokens":1}'

# Keep the script running while the container runs.
docker wait "${NAME}" >/dev/null
