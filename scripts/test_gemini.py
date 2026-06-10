"""Test LLM API call locally via litellm."""
import os
import litellm
import time
from dotenv import load_dotenv

load_dotenv()

litellm.suppress_debug_info = True
litellm.set_verbose = False

OPENAI_KEY = os.environ["OPENAI_API_KEY"]

print("Testing openai/gpt-4o-mini...")
t0 = time.monotonic()
try:
    resp = litellm.completion(
        model="openai/gpt-4o-mini",
        messages=[{"role": "user", "content": "In short, what is 2+2?"}],
        api_key=OPENAI_KEY,
        timeout=15,
    )
    elapsed = int((time.monotonic() - t0) * 1000)
    print(f"SUCCESS in {elapsed}ms: {resp.choices[0].message.content[:100]}")
    print(f"Usage: {resp.usage}")
except Exception as e:
    elapsed = int((time.monotonic() - t0) * 1000)
    print(f"ERROR after {elapsed}ms: {type(e).__name__}: {str(e)[:300]}")
