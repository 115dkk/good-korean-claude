"""Stop hook: 사용자가 읽는 최종 답변(보고)을 검사한다.

block_emdash.py(마크다운 파일 쓰기 검사)의 짝이다. 다음 중 하나라도 해당하면
턴을 끝내지 못하게 막고 다시 쓰게 한다.
- em-dash(U+2014) 또는 줄표(U+2015)가 있다. 하이픈(-)과 en-dash(U+2013)는 검사하지 않는다.
- 단어 수 기준 영어 비율이 80% 이상이다. 한글이 섞인 단어는 한국어로 센다.

코드블록, 인라인 코드, URL, 경로, 마크다운 링크, HTML 태그는 검사 전에 지운다.
인용(>) 줄은 영어 비율 계산에서만 뺀다. 같은 턴에서 3번 막아도 고쳐지지 않으면 통과시킨다.
"""

import json
import os
import re
import sys
import tempfile

EN_RATIO_LIMIT = 0.8
MAX_BLOCKS = 3

DASHES = re.compile("[\u2014\u2015]")
HANGUL = re.compile("[\uac00-\ud7a3\u3131-\u318e\u1100-\u11ff]")
LATIN = re.compile("[A-Za-z]")
W = "A-Za-z0-9_"  # ASCII 단어 문자 (파이썬의 \w는 한글도 잡는다)

# 같은 턴에서 막은 횟수를 세션마다 적어 둔다. 플러그인 데이터 디렉터리가 없으면 임시 디렉터리에 둔다.
STATE_DIR = os.path.join(
    os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(tempfile.gettempdir(), "good-korean-claude"),
    "report-guard",
)


def drop_fences(text):
    out, fence = [], None
    for line in text.split("\n"):
        m = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is None:
            if m:
                fence = m.group(1)
                continue
            out.append(line)
        elif m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and line.strip() == m.group(1):
            fence = None
    return "\n".join(out)


def prose(text):
    s = drop_fences(text.replace("\r\n", "\n").replace("\r", "\n"))
    s = re.sub(r"``[^\n]*?``|`[^`\n]*`", " ", s)
    s = re.sub(r"!?\[[^\]\n]*\]\([^)\n]*\)", " ", s)
    s = re.sub(r"<(?:https?|ftp|file|computer):[^>\s]*>", " ", s, flags=re.I)
    s = re.sub(r"\b(?:https?|ftp|file|computer)://\S+", " ", s, flags=re.I)
    s = re.sub(r"\bwww\.\S+", " ", s, flags=re.I)
    s = re.sub(rf"[{W}.+-]+@[{W}-]+\.[{W}.-]+", " ", s)
    s = re.sub(r"</?[A-Za-z][^>\n]*>", " ", s)
    # 유닉스 경로와 윈도우 경로(C:\..., .\..., ~\...)
    s = re.sub(rf"(?:^|(?<=[\s(]))(?:~|\.{{1,2}})?/?(?:[{W}.@-]+/)+[{W}.@-]*", " ", s, flags=re.M)
    s = re.sub(rf"(?:^|(?<=[\s(]))(?:[A-Za-z]:|~|\.{{1,2}})?\\?(?:[{W}.@-]+\\)+[{W}.@-]*", " ", s, flags=re.M)
    return s


def judge(text):
    p = prose(text)
    dashes = len(DASHES.findall(p))
    unquoted = "\n".join(l for l in p.split("\n") if not re.match(r"^\s{0,3}>", l))
    en = ko = 0
    for tok in unquoted.split():
        if HANGUL.search(tok):
            ko += 1
        elif LATIN.search(tok):
            en += 1
    total = en + ko
    ratio = en / total if total else 0.0
    return {
        "dashes": dashes,
        "en": en,
        "ko": ko,
        "ratio": ratio,
        "dash_bad": dashes > 0,
        "english_bad": en > 0 and ratio >= EN_RATIO_LIMIT,
    }


def reason_for(v):
    lines = ["[보고 검사] 방금 최종 답변이 사용자에게 보이는 본문 검사에 걸렸습니다."]
    if v["dash_bad"]:
        lines.append(
            f"- em-dash(U+2014) 또는 줄표(U+2015)가 {v['dashes']}개 있습니다. "
            "쉼표, 마침표, 괄호, 콜론으로 바꾸거나 문장을 나누세요."
        )
    if v["english_bad"]:
        lines.append(
            f"- 영어 비율이 {round(v['ratio'] * 100)}%입니다(영어 단어 {v['en']}개, "
            f"한국어 단어 {v['ko']}개, 기준 {round(EN_RATIO_LIMIT * 100)}%). 본문을 한국어로 쓰세요."
        )
    lines.append(
        "코드블록, 인라인 코드, URL, 경로, 마크다운 링크, 인용(>) 줄은 검사에서 빠집니다. "
        "영어 원문을 꼭 보여야 하면 인용이나 코드블록에 넣으세요."
    )
    lines.append("고친 답변 전체를 다시 쓰세요. 사과나 이 검사에 대한 언급 없이 본문만 쓰세요.")
    return "\n".join(lines)


def last_text_from_transcript(path):
    """last_assistant_message가 없을 때: 이번 턴의 마지막 assistant 텍스트."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            rows = f.readlines()
    except OSError:
        return ""
    for raw in reversed(rows):
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        msg = row.get("message") or {}
        if row.get("type") == "user" and not row.get("isMeta"):
            content = msg.get("content")
            if isinstance(content, str) or any(
                isinstance(b, dict) and b.get("type") == "text" for b in (content or [])
            ):
                return ""
        if row.get("type") != "assistant":
            continue
        parts = [b.get("text", "") for b in msg.get("content") or [] if isinstance(b, dict) and b.get("type") == "text"]
        if parts:
            return "\n".join(parts)
    return ""


def counter(session_id, update=None):
    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "default") + ".count")
    if update is None:
        try:
            with open(path, encoding="utf-8") as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(update))
    return update


def emit(obj):
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")


def main():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    sid = str(data.get("session_id") or "")
    if not data.get("stop_hook_active"):
        counter(sid, 0)
    text = data.get("last_assistant_message")
    if text is None:
        text = last_text_from_transcript(str(data.get("transcript_path") or ""))
    if not text or not str(text).strip():
        return 0
    v = judge(str(text))
    if not (v["dash_bad"] or v["english_bad"]):
        counter(sid, 0)
        return 0
    n = counter(sid)
    if n >= MAX_BLOCKS:
        counter(sid, 0)
        emit({"systemMessage": f"보고 검사: {MAX_BLOCKS}번 다시 써도 기준을 넘지 못해 이번 답변은 통과시켰습니다."})
        return 0
    counter(sid, n + 1)
    emit({"decision": "block", "reason": reason_for(v)})
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # 형식 검사 훅은 실패하면 통과시킨다. 턴을 붙잡아 두면 안 된다.
        sys.exit(0)
