"""Shared instruction-override patterns for user input and retrieved evidence."""

import re

INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(?:all\s+)?previous\s+instructions", re.I),
    re.compile(r"reveal\s+(?:the\s+)?system\s+prompt", re.I),
    re.compile(r"reveal\s+restricted\s+records", re.I),
    re.compile(r"\bcall\s+(?:this|any|the)\s+(?:available\s+)?(?:admin\s+)?tool\b", re.I),
    re.compile(r"\bsend\s+(?:the\s+)?data\s+to\b", re.I),
    re.compile(r"\byou\s+are\s+now\b", re.I),
    re.compile(r"\bdisregard\s+(?:all\s+)?(?:prior|previous)\s+(?:rules|instructions)\b", re.I),
    re.compile(r"\boverride\s+(?:the\s+)?(?:security|access)\s+(?:policy|controls)\b", re.I),
)
