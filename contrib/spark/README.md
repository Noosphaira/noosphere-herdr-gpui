# DGX Spark inference files

Applied on 2026-10-08 (all three steps). Measured with `bench.py`, single
stream, thinking off: Qwen3.8-27B 10.0 tok/s before MTP, 19.3 with it;
Qwen3.6-35B-A3B 56.9 tok/s. Tool calls work on both.

**These files belong on the DGX Spark, not on this PC.** They are kept here
so changes to the Spark can be reviewed before they are made. `apply.sh` and
`bench.py` are the exceptions: they run on this PC and reach the Spark over
SSH or HTTP.

| File | On the Spark | What it is |
|---|---|---|
| `vllm-qwen38.sh.current` | `~/vllm-qwen38.sh` | The script as it was on 2026-10-08 |
| `vllm-qwen38.sh` | `~/vllm-qwen38.sh` | Proposed: adds MTP speculative decoding (3 tokens) and cached-token reporting |
| `vllm-qwen36-moe.sh` | `~/vllm-qwen36-moe.sh` | Proposed: Qwen3.6-35B-A3B NVFP4 for coding agents, same image, port 11502 |
| `llama-swap-config.yaml.current` | `~/.config/llama-swap/config.yaml` | The config as it was on 2026-10-08 |
| `llama-swap-config.yaml` | `~/.config/llama-swap/config.yaml` | Proposed: both models, swap mode |

How llama-swap uses them: a request names a model; llama-swap runs that
model's script (one vLLM container) and stops the other model first. Only one
model is loaded at a time, so switching takes as long as a cold start.

Apply, one step at a time, from this PC:

```
contrib/spark/apply.sh mtp        # then: python3 contrib/spark/bench.py orcarouter/Qwen3.8-27B-Uncensored-NVFP4
contrib/spark/apply.sh download   # ~20-25 GB from Hugging Face
contrib/spark/apply.sh moe        # then: python3 contrib/spark/bench.py RedHatAI/Qwen3.6-35B-A3B-NVFP4
```

If the Qwen3.8 model fails to load with MTP, the vLLM build may accept only
one speculative token for this hybrid model: change `"num_speculative_tokens":3`
to `1`. Every replaced file is backed up beside itself as `<file>.bak-<date>`.
