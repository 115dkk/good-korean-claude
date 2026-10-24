"""PreToolUse hook: 한국어 마크다운에 em-dash(U+2014)가 들어가는 것을 막는다.

Write/Edit이 .md/.markdown/.mdx 파일을 건드릴 때만 검사한다. 새로 쓰는 텍스트나
대상 파일에 한글이 있고 그 텍스트에 em-dash가 있으면 deny를 반환한다.
영어 문서의 em-dash는 통과시킨다.
"""

import json
import re
import sys

EM_DASH = "\u2014"
HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]")
MD_SUFFIXES = (".md", ".markdown", ".mdx")


def read_payload():
    try:
        raw = sys.stdin.buffer.read()
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None


def main():
    payload = read_payload()
    if not isinstance(payload, dict):
        return 0

    tool = payload.get("tool_name") or ""
    if tool not in ("Write", "Edit"):
        return 0

    tool_input = payload.get("tool_input") or {}
    path = str(tool_input.get("file_path") or "")
    if not path.lower().endswith(MD_SUFFIXES):
        return 0

    if tool == "Write":
        new_text = str(tool_input.get("content") or "")
    else:
        new_text = str(tool_input.get("new_string") or "")

    if EM_DASH not in new_text:
        return 0

    korean = bool(HANGUL.search(new_text))
    if not korean:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                korean = bool(HANGUL.search(handle.read()))
        except OSError:
            korean = False
    if not korean:
        return 0

    reason = (
        "한국어 마크다운에 em-dash(U+2014)가 들어 있습니다. 한국어에는 "
        "em-dash 용법이 없으므로 쓰지 않습니다. 삽입절은 괄호로 처리하거나 "
        "문장을 나눈 뒤 다시 쓰십시오. 대상 파일: " + path
    )
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.buffer.write(json.dumps(out, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
