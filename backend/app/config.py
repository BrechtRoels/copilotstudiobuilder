import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

GENAI_BASE_URL = os.getenv("GENAI_BASE_URL", "https://genai-sharedservice-emea.pwc.com")
GENAI_API_KEY = os.getenv("GENAI_API_KEY", "")
GENAI_API_VERSION = os.getenv("GENAI_API_VERSION", "")
# Authoring valid Copilot Studio YAML benefits from a strong model; summaries can use a cheap one.
GENAI_LLM_MODEL = os.getenv("GENAI_LLM_MODEL", "openai.gpt-4.1")
GENAI_FAST_MODEL = os.getenv("GENAI_FAST_MODEL", "openai.gpt-4.1-mini")
USE_MOCK_AI = os.getenv("USE_MOCK_AI", "false").lower() == "true"
# Output cap per LLM call. The service default (4096) truncates multi-file proposals
# (an agent + topic + several flows), which then surfaces as "invalid JSON".
GENAI_MAX_OUTPUT_TOKENS = int(os.getenv("GENAI_MAX_OUTPUT_TOKENS", "32000"))

# Max LLM repair attempts after a generated file fails validation.
AI_REPAIR_ATTEMPTS = int(os.getenv("AI_REPAIR_ATTEMPTS", "2"))
# Budget (chars) for the full text of existing workspace files in the prompt, so follow-up
# requests edit what is there instead of rewriting blind. Files beyond it are listed by name only.
AI_CONTEXT_CHARS = int(os.getenv("AI_CONTEXT_CHARS", "60000"))
# After generating, check each requirement in the request against the files (one extra LLM call).
AI_COVERAGE_CHECK = os.getenv("AI_COVERAGE_CHECK", "true").lower() == "true"

WORKSPACES_DIR = Path(os.getenv("WORKSPACES_DIR", ROOT_DIR / "workspaces")).resolve()
KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge" / "mcs"

# Optional: local checkout of microsoft/skills-for-copilot-studio, used for Validate/Pull/Push.
MCS_SKILLS_DIR = os.getenv("MCS_SKILLS_DIR", "")

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
