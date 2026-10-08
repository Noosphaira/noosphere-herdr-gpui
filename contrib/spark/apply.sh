#!/usr/bin/env bash
# Run on THIS PC; it changes the DGX Spark over SSH. Each step is separate so
# it can be approved and run on its own:
#
#   apply.sh mtp        Qwen3.8 script gains MTP and cached-token reporting
#   apply.sh download   fetch RedHatAI/Qwen3.6-35B-A3B-NVFP4 into the model cache
#   apply.sh moe        install ~/vllm-qwen36-moe.sh and the new llama-swap
#                       config, then restart llama-swap (stops any loaded model)
#
# Every file it replaces is first copied to <file>.bak-<date>.
set -euo pipefail
spark="${SPARK:-spark-dbf1.local}"
here="$(cd "$(dirname "$0")" && pwd)"
stamp="$(date +%Y-%m-%d-%H%M)"

backup() { ssh "$spark" "[ ! -e $1 ] || cp -p $1 $1.bak-$stamp"; }

case "${1:-}" in
  mtp)
    backup '~/vllm-qwen38.sh'
    scp -q "$here/vllm-qwen38.sh" "$spark:vllm-qwen38.sh"
    echo "Qwen3.8 script updated; MTP applies from its next load."
    ;;
  download)
    # Uses the image's own Hugging Face client, writing into the model cache
    # the scripts mount read-only.
    ssh "$spark" 'docker run --rm --entrypoint "" \
      -v "$HOME/.cache/huggingface/hub:/root/.cache/huggingface/hub" \
      vllm/vllm-openai:qwen38 hf download RedHatAI/Qwen3.6-35B-A3B-NVFP4'
    ;;
  moe)
    backup '~/.config/llama-swap/config.yaml'
    scp -q "$here/vllm-qwen36-moe.sh" "$spark:vllm-qwen36-moe.sh"
    scp -q "$here/llama-swap-config.yaml" "$spark:.config/llama-swap/config.yaml"
    ssh "$spark" 'chmod +x ~/vllm-qwen36-moe.sh && systemctl --user restart llama-swap && sleep 2 && systemctl --user is-active llama-swap'
    ;;
  *)
    sed -n '2,12p' "$0"
    exit 2
    ;;
esac
