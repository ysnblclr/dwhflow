"""İsteğe bağlı LLM katmanı: OpenAI uyumlu istemci + kanıt denetimli DAF çıkarımı.

* ``extract_with_llm`` — serbest metinden ``dwhflow.extract/2`` (tablo, süreç, kolon eşlemeli
  akış, bağımlılık, belirsiz cümle); v1 yanıtları da kabul edilir.
* ``suggest_table_roles`` / ``suggest_for_unrecognized`` — tanınmayan tablo başlığı eşleme.
* ``list_prompts`` / ``load_prompt`` / ``save_prompt_version`` — istem kütüphanesi.
* ``run_console`` — istem test konsolu; ``mask_text`` — kişisel veri maskeleme;
  ``evaluate`` — altın set üzerinde kesinlik/duyarlılık.
"""

from .client import LLMClient, LLMConfig, LLMError, parse_json_reply
from .console import run_console
from .evaluate import evaluate, load_golden
from .extract import LLMStats, extract_with_llm, review_reply, validate_flow
from .mask import mask_text, should_mask
from .prompts import PromptError, list_prompts, load_prompt, save_prompt_version
from .table_roles import (
    TableRoleSuggestion,
    review_table_roles,
    roles_to_synonyms,
    suggest_for_unrecognized,
    suggest_table_roles,
)

__all__ = [
    "LLMClient",
    "LLMConfig",
    "LLMError",
    "LLMStats",
    "PromptError",
    "TableRoleSuggestion",
    "evaluate",
    "extract_with_llm",
    "list_prompts",
    "load_golden",
    "load_prompt",
    "mask_text",
    "parse_json_reply",
    "review_reply",
    "review_table_roles",
    "roles_to_synonyms",
    "run_console",
    "save_prompt_version",
    "should_mask",
    "suggest_for_unrecognized",
    "suggest_table_roles",
    "validate_flow",
]
