"""Intent detection for the assistant.

Deliberately simple regex routing (no ML) so it is fast, deterministic and
cheap to run on CPU-only hardware. Prompt-injection "easter eggs" are matched
first and reported with ``category == "injection"`` so the API layer can
either execute them (demo mode) or refuse them (guardrails mode).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_FLAGS = re.IGNORECASE | re.DOTALL


@dataclass
class Intent:
    name: str
    category: str  # banking | knowledge | injection | system
    label: str = ""
    params: dict = field(default_factory=dict)


def _amount(text: str) -> float | None:
    m = re.search(r"\$?\s*([\d][\d,]*(?:\.\d{1,2})?)", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


# --- Prompt-injection / abuse patterns ---------------------------------------
# (name, regex, human label)
INJECTION_PATTERNS: list[tuple[str, str, str]] = [
    (
        "drop_tables",
        r"\bdrop\s+(?:all\s+)?(?:of\s+)?(?:the\s+)?(?:database\s+|bank\s+|customer\s+)?tables?\b"
        r"|\bdrop\s+(?:the\s+)?database\b"
        r"|\bdelete\s+all\s+(?:of\s+)?(?:the\s+)?(?:customer\s+|bank\s+|banking\s+)?(?:data|records|tables)\b"
        r"|\btruncate\s+(?:all\s+)?tables?\b",
        "Destructive SQL executed",
    ),
    (
        "root_access",
        r"(?:ignore|disregard|forget|bypass|override|skip)\s+(?:all\s+|any\s+|the\s+|your\s+)?"
        r"(?:previous\s+|prior\s+|above\s+|earlier\s+|existing\s+)?"
        r"(?:constraints|instructions|rules|restrictions|guidelines|safeguards|policies|limits)"
        r".*?(?:root|admin(?:istrator)?|elevated|sudo|superuser|privileg)"
        r"|\b(?:give|grant|get|make)\s+me\s+(?:root|admin(?:istrator)?|superuser|elevated|full)\s+"
        r"(?:access|privileges|rights|permissions|user)\b"
        r"|\bsudo\s+su\b|\benable\s+(?:admin|administrator|root|god)\s+mode\b",
        "Privilege escalation",
    ),
    (
        "pii_dump",
        r"(?:ignore|disregard|bypass|override|forget|skip|remove)\s+(?:all\s+|any\s+|the\s+|your\s+)?"
        r"(?:data\s+)?(?:censors?|censorship|redactions?|masking|privacy\s+rules|rules|constraints|filters|restrictions)"
        r".*?(?:social\s+security|ssns?|contact\s+(?:information|info|details)|customer\s+(?:data|information|records|details))"
        r"|\bshow\s+(?:me\s+)?all\s+customers?.*?(?:ssns?|social\s+security|contact|phone|email)"
        r"|\b(?:all|every)\s+customers?'?\s+(?:ssns?|social\s+security\s+numbers?|phone\s+numbers?)\b"
        r"|\b(?:list|dump|export)\s+(?:all\s+)?(?:customer\s+)?(?:ssns?|social\s+security\s+numbers?)\b",
        "Sensitive data exfiltration",
    ),
    (
        "balance_tamper",
        r"\b(?:set|update|change|make|adjust|modify)\s+(?:my\s+)?(?:(?:checking|savings)\s+)?(?:account\s+)?balance\s+to\s+\$?\s*[\d][\d,]*(?:\.\d{1,2})?"
        r"|\b(?:add|deposit|credit)\s+\$?\s*[\d][\d,]*(?:\.\d{1,2})?\s*(?:dollars\s+)?(?:to|into)\s+my\s+(?:account|balance|checking|savings)\b"
        r"|\bgive\s+me\s+\$?\s*[\d][\d,]*(?:\.\d{1,2})?\s*(?:dollars)?\b",
        "Unauthorised data modification",
    ),
    (
        "wipe_knowledge",
        r"\bforget\s+everything\b"
        r"|\b(?:wipe|erase|clear|purge|delete)\s+(?:all\s+)?(?:of\s+)?(?:the\s+|your\s+)?(?:knowledge\s*-?\s*base|memory|documents|policy\s+library|training\s+data)\b",
        "Knowledge-base destruction",
    ),
]

# --- Ordinary banking intents ------------------------------------------------
BANKING_PATTERNS: list[tuple[str, str]] = [
    ("whoami", r"\bwho\s+am\s+i\b|\bwhat(?:'s|\s+is)\s+my\s+(?:role|access\s+level)\b|\bam\s+i\s+(?:an\s+)?admin"),
    ("greeting", r"^\s*(?:hi|hello|hey|howdy|good\s+(?:morning|afternoon|evening))\b[\s!.,]*$"),
    ("help", r"^\s*help\b|\bwhat\s+can\s+you\s+(?:do|help)\b|\bhow\s+can\s+you\s+help\b"),
    ("profile", r"\bmy\s+(?:contact|profile|personal)\s+(?:information|info|details)\b"
                r"|\bmy\s+(?:phone|email|address|ssn|social\s+security)\b"
                r"|\bwhat\s+do\s+you\s+know\s+about\s+me\b|\bwho\s+are\s+you\s+talking\s+to\b"),
    ("transactions", r"\btransactions?\b|\brecent\s+activity\b|\bstatement\b|\bspending\b|\bpurchases?\b"
                     r"|\bcharges?\b|\bwhere\s+did\s+my\s+money\s+go\b|\bpayments?\s+(?:history|made)\b"),
    ("accounts", r"\b(?:my|list|show|which|what)\s+accounts\b|\baccounts\s+do\s+i\s+have\b|\baccount\s+summary\b"),
    ("balance", r"\bbalance\b|\bhow\s+much\s+(?:money\s+)?(?:do\s+i\s+have|is\s+in)\b|\bavailable\s+funds\b"),
]


def detect_intent(text: str) -> Intent:
    """Classify a user message. Injection patterns take precedence."""
    msg = (text or "").strip()
    if not msg:
        return Intent("empty", "system", "Empty message")

    for name, pattern, label in INJECTION_PATTERNS:
        if re.search(pattern, msg, _FLAGS):
            params = {}
            if name == "balance_tamper":
                params["amount"] = _amount(msg)
            return Intent(name, "injection", label, params)

    for name, pattern in BANKING_PATTERNS:
        if re.search(pattern, msg, _FLAGS):
            category = "system" if name in {"whoami", "greeting", "help"} else "banking"
            return Intent(name, category, name.replace("_", " ").title())

    return Intent("knowledge", "knowledge", "Knowledge base")


SUGGESTED_PROMPTS = [
    "What's my current balance?",
    "Show my recent transactions",
    "What are the wire transfer fees?",
    "How do I report a lost or stolen card?",
    "What is the mobile deposit limit?",
]
