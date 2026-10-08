#!/usr/bin/env python3
"""Single-stream decode speed and tool calling of one llama-swap model.

Run on THIS PC:  python3 contrib/spark/bench.py <model-name> [base-url]

Loads the model if needed (the first request can take minutes), then:
- streams a 512-token answer with thinking off and reports tokens/second,
- checks that a tool definition comes back as a structured tool call.
"""

import json
import sys
import time
import urllib.request

model = sys.argv[1]
base = sys.argv[2] if len(sys.argv) > 2 else "http://192.168.18.111:11500/v1"


def post(body, stream=False):
    request = urllib.request.Request(
        f"{base}/chat/completions",
        json.dumps({"model": model, **body}).encode(),
        {"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(request, timeout=1200)


# Warm-up: loads the model and fills the prefix cache.
post({"messages": [{"role": "user", "content": "Say ok."}], "max_tokens": 8,
      "chat_template_kwargs": {"enable_thinking": False}}).read()

prompt = "Write a long, detailed explanation of how a hash map works, with examples."
start = first = None
count = 0
with post({"messages": [{"role": "user", "content": prompt}], "max_tokens": 512, "stream": True,
           "chat_template_kwargs": {"enable_thinking": False},
           "stream_options": {"include_usage": True}}) as response:
    start = time.monotonic()
    for raw in response:
        line = raw.decode().strip()
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        chunk = json.loads(line[6:])
        if chunk.get("usage"):
            count = chunk["usage"]["completion_tokens"]
        if chunk.get("choices") and chunk["choices"][0]["delta"].get("content") and first is None:
            first = time.monotonic()
end = time.monotonic()
print(f"{model}: {count} tokens, {count / (end - first):.1f} tok/s decode, first token {first - start:.2f}s")

tools = [{"type": "function", "function": {"name": "get_weather", "description": "Weather for a city",
          "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]
answer = json.load(post({"messages": [{"role": "user", "content": "Weather in Zagreb? Use the tool."}],
                         "tools": tools, "max_tokens": 1024}))
calls = answer["choices"][0]["message"].get("tool_calls")
print("tool call:", json.dumps(calls[0]["function"]) if calls else "NONE (broken)")
