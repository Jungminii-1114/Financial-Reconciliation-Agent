"""Optional local text model. Only typed financial counts enter its prompt."""

import json
import os
import re
import subprocess

from ..tools.errors import ToolError


def ollama_advice(model: str, facts: dict) -> str:
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:-]{0,127}", model) is None:
        raise ToolError("MODEL_UNAVAILABLE", "로컬 모델 이름이 올바르지 않습니다.")
    env = {**os.environ, "OLLAMA_HOST": "http://127.0.0.1:11434", "OLLAMA_NOHISTORY": "1"}
    prompt = (
        'Return exactly one JSON object {"action":"WAIT|ESCALATE|REPROCESS"} using one value. '
        'When ingestion is incomplete choose WAIT. Otherwise choose ESCALATE for any transaction '
        'discrepancy or a passed cutoff. Otherwise propose REPROCESS for a nonempty fully matching batch. '
        'This is a proposal and has no approval authority. Facts: '
        + json.dumps(facts, sort_keys=True)
    )
    try:
        models = subprocess.run(["ollama", "list"], env=env, timeout=5, capture_output=True, text=True, check=True)
        available = {line.split()[0] for line in models.stdout.splitlines()[1:] if line.split()}
        if model not in available and model + ":latest" not in available:
            raise ToolError("MODEL_UNAVAILABLE", "이미 설치된 로컬 Ollama 모델이 필요합니다.")
        reply = subprocess.run(["ollama", "run", model, "--format", "json", "--hidethinking", "--nowordwrap"],
                               input=prompt, env=env, timeout=90, capture_output=True, text=True, check=True)
        if len(reply.stdout) > 4096:
            raise ValueError("oversized model response")
        data = json.loads(reply.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ToolError("MODEL_UNAVAILABLE", "로컬 모델의 구조화 제안을 받지 못했습니다.") from exc
    if type(data) is not dict or set(data) != {"action"} or data["action"] not in ("WAIT", "ESCALATE", "REPROCESS"):
        raise ToolError("INVALID_MODEL_PROPOSAL", "로컬 모델의 제안 형식이 올바르지 않습니다.")
    return data["action"]
