"""PreToolUse 훅: 파일에 들어갈 한국어 문장을 별도 모델이 규칙과 대조해 판정한다.

Write/Edit/MultiEdit이 새로 들이는 줄 가운데 한글이 든 줄과 그 앞뒤 세 줄 안의 새 줄을
골라 판정 모델(기본 Claude Sonnet 5.5, effort medium)에게 보낸다. 앞뒤 줄을 함께 보내야
판정 모델이 한글 문구 옆의 코드까지 본다. 한국어 이름표 바로 아래 줄에서 예외 문자열을
그대로 띄우는 코드가 그런 예다. 한글이 든 새 줄이 없으면 모델을 부르지 않는다.
규칙에 분명히 걸리는 표현이 있으면 deny를 돌려주며, 걸린 표현을 각각 하나씩 어긴 규칙,
고칠 말과 함께 적는다. 같은 규칙을 여러 군데에서 어겼으면 그 수만큼 적는다. 그 밖에는
아무것도 출력하지 않아 도구 호출이 그대로 진행된다. 판정 모델 호출이 실패하면 막지 않고 통과시키되 그
사실을 systemMessage로 알린다.

인자
  --rules PATH       규칙 원문 파일. ~ 는 홈 디렉터리로 바꾼다.
  --section HEADING  그 파일에서 이 제목으로 시작하는 절만 쓴다(같거나 높은 단계의
                     다음 제목 앞까지). 없으면 파일 전체를 쓴다.
  --scope copy|korean
                     copy: 화면에 보이는 문구, README, 사용자에게 공개하는 문서
                     페이지(처리방침, 약관, 도움말)를 판정한다(write.md 대상).
                     마크다운이 공개 페이지인지는 판정 모델이 가리고, 에이전트
                     지침 파일은 모델을 부르지 않고 통과시킨다.
                     korean: 파일 속 한국어 문장 전부를 판정한다.
  --label TEXT       거부 사유 첫 줄에 붙일 이름.
  --model ID, --effort LEVEL
                     판정 모델과 effort. 기본값은 claude-sonnet-5-5, medium.

같은 스크립트를 로컬 훅과 플러그인 훅이 함께 쓴다. 판정 모델은 사용자의 claude CLI로
부르며, 그 호출은 설정 파일의 훅과 MCP 서버를 읽지 않고 도구도 쓰지 않는다.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

JUDGE_TIMEOUT = 150
MAX_CHARS = 20000
CONTEXT = 3
GUARD_ENV = "KOREAN_JUDGE_ACTIVE"

HANGUL = re.compile(r"[가-힣ㄱ-ㆎ]")
HOME = os.path.expanduser("~")
MD_SUFFIXES = (".md", ".markdown", ".mdx")
TEST_DIRS = {"test", "tests", "__tests__", "__snapshots__", "testdata", "fixtures"}
TEST_NAME = re.compile(r"(^tests?\.rs$|_tests?\.[a-z]+$|\.(test|spec)\.[a-z]+$|^test_.*\.py$)", re.I)
LOCKFILES = {"package-lock.json", "cargo.lock", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"}
AGENT_FILES = {"claude.md", "agents.md", "gemini.md", "skill.md"}

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["allow", "deny"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "rule": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["quote", "rule", "fix"],
            },
        },
    },
    "required": ["verdict", "findings"],
}

SCOPES = {
    "copy": (
        "In scope means text the rules below govern: words a user interface shows to its users "
        "(labels, buttons, descriptions, helper text, tooltips, empty states, dialogs, notifications, "
        "toasts, status lines, error messages, installer, landing and login pages, localization "
        "catalog entries), the prose of a README, and pages published for users from Markdown or "
        "HTML, such as a privacy policy, terms of use, or a help or documentation site. Out of "
        "scope: code comments, developer log lines, identifiers, test names and fixtures, commit "
        "messages, internal design documents and plans, and anything you cannot tell is shown to "
        "users."
    ),
    "korean": (
        "In scope means every Korean sentence or phrase written in the file: documents, READMEs, "
        "code comments, user-facing strings, messages and notes. Out of scope: identifiers and "
        "keys, text quoted verbatim from another source (a quotation, a cited title, a test input "
        "that is meant to be wrong), and non-Korean text."
    ),
}

INSTRUCTIONS = """You are a gate that judges Korean text before an AI coding agent writes it to a file.
You receive the file path, the lines the edit adds that contain Hangul, and the other lines it
adds within {context} lines of them. The nearby lines show the code around the copy, such as a
raw error rendered right below a Korean label; judge them like any other line.

Step 1. Decide which of those lines are in scope. {scope} If no line is in scope, answer
verdict "allow" with no findings.

Step 2. For in-scope lines only, look for expressions that clearly break a specific rule in the
rule text below. Answer "deny" only for clear breaches you can name. When you are unsure,
answer "allow". Style preferences that no rule states are not breaches. Report each clear breach
separately: when the same rule is broken in several places, give one finding for each of them,
and read every given line to the end.

Each finding must quote the offending expression exactly as it appears in the given lines (an
exact substring, not a paraphrase), name the rule it breaks, and give a corrected wording. Write
"rule" and "fix" in Korean, and follow the rules yourself when you write them.

=== Rules: {label} ===
{rules}
"""


def parse_args(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--rules", required=True)
    parser.add_argument("--section")
    parser.add_argument("--scope", choices=sorted(SCOPES), required=True)
    parser.add_argument("--label", default="규칙 판정")
    parser.add_argument("--model", default="claude-sonnet-5-5")
    parser.add_argument("--effort", default="medium")
    return parser.parse_args(argv)


def read_payload():
    try:
        raw = sys.stdin.buffer.read()
        return json.loads(raw.decode("utf-8", errors="replace")) if raw else None
    except Exception:
        return None


def emit(obj):
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.write(b"\n")


def skipped_path(path, scope):
    norm = os.path.normcase(os.path.abspath(path))
    for root in (os.path.join(HOME, ".claude"), os.path.join(HOME, ".codex")):
        if norm.startswith(os.path.normcase(root) + os.sep):
            return True
    name = os.path.basename(path).lower()
    parts = {part.lower() for part in re.split(r"[\\/]", path)}
    if ".git" in parts or name in LOCKFILES:
        return True
    if scope == "copy":
        # 에이전트에게 주는 지침 파일은 사용자가 읽는 글이 아니다. 나머지 마크다운은 모델이 가린다.
        if name.endswith(MD_SUFFIXES) and (name in AGENT_FILES or parts & {".claude", "skills"}):
            return True
        if parts & TEST_DIRS or parts & {".github", ".superloopy"} or TEST_NAME.search(name):
            return True
    return False


def added_lines(tool, tool_input, path):
    """이전에 없던 새 줄 가운데 한글이 든 줄과 그 앞뒤 CONTEXT줄 안의 줄을 돌려준다.

    빈 줄은 뺀다. 한글이 든 새 줄이 없으면 빈 목록을 돌려준다.
    """
    pairs = []
    if tool == "Write":
        old = ""
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                old = handle.read()
        except OSError:
            pass
        pairs.append((old, str(tool_input.get("content") or "")))
    elif tool == "Edit":
        pairs.append((str(tool_input.get("old_string") or ""), str(tool_input.get("new_string") or "")))
    elif tool == "MultiEdit":
        for edit in tool_input.get("edits") or []:
            if isinstance(edit, dict):
                pairs.append((str(edit.get("old_string") or ""), str(edit.get("new_string") or "")))
    lines = []
    for old, new in pairs:
        before = set(old.splitlines())
        new_lines = new.splitlines()
        fresh = [i for i, line in enumerate(new_lines) if line.strip() and line not in before]
        near = set()
        for i in fresh:
            if HANGUL.search(new_lines[i]):
                near.update(range(i - CONTEXT, i + CONTEXT + 1))
        lines.extend(new_lines[i] for i in fresh if i in near)
    return lines


def load_rules(path, section):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return ""
    if not section:
        return text
    lines = text.splitlines()
    level = len(section) - len(section.lstrip("#"))
    for start, line in enumerate(lines):
        if line.strip() == section.strip():
            for end in range(start + 1, len(lines)):
                heading = re.match(r"^(#+)\s", lines[end])
                if heading and len(heading.group(1)) <= level:
                    return "\n".join(lines[start:end])
            return "\n".join(lines[start:])
    return ""


def judge(args, path, lines, rules):
    claude = shutil.which("claude")
    if not claude:
        return None, "claude 실행 파일을 PATH에서 찾지 못했습니다"
    body = "\n".join(lines)
    truncated = len(body) > MAX_CHARS
    body = body[:MAX_CHARS]
    # 규칙 원문은 Windows 명령줄 한도(32767자)를 넘을 수 있으므로 표준 입력으로 넘긴다.
    prompt = (rules + "\n\n=== Text to judge ===\nFile: " + path + "\nAdded lines with Hangul and the added lines near them"
              + (" (truncated)" if truncated else "") + ":\n" + body)
    command = [
        claude, "-p", "--model", args.model, "--effort", args.effort,
        "--tools", "", "--setting-sources", "local", "--strict-mcp-config",
        "--no-session-persistence", "--output-format", "json",
        "--json-schema", json.dumps(SCHEMA),
        "--system-prompt", "You are a gate that judges Korean text against the rules given in the message. Answer only through the structured output.",
    ]
    try:
        result = subprocess.run(
            command, input=prompt.encode("utf-8"), capture_output=True, timeout=JUDGE_TIMEOUT,
            cwd=tempfile.gettempdir(), env=dict(os.environ, **{GUARD_ENV: "1"}),
        )
    except subprocess.TimeoutExpired:
        return None, "판정이 " + str(JUDGE_TIMEOUT) + "초 안에 끝나지 않았습니다"
    except OSError as error:
        return None, "판정 모델을 실행하지 못했습니다: " + str(error)
    try:
        reply = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except Exception:
        tail = result.stderr.decode("utf-8", errors="replace")[-300:]
        return None, "판정 결과를 읽지 못했습니다(종료 코드 " + str(result.returncode) + "): " + tail
    verdict = reply.get("structured_output") if isinstance(reply, dict) else None
    if not isinstance(reply, dict) or reply.get("is_error") or not isinstance(verdict, dict):
        detail = reply.get("result") or reply.get("subtype") if isinstance(reply, dict) else reply
        return None, "판정 모델이 오류를 돌려주었습니다: " + str(detail)[:300]
    return verdict, None


def main(argv):
    if os.environ.get(GUARD_ENV):
        return 0
    args = parse_args(argv)
    payload = read_payload()
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Write", "Edit", "MultiEdit"):
        return 0
    tool_input = payload.get("tool_input") or {}
    path = str(tool_input.get("file_path") or "")
    if not path or skipped_path(path, args.scope):
        return 0
    lines = added_lines(payload["tool_name"], tool_input, path)
    if not lines:
        return 0

    rule_text = load_rules(args.rules, args.section)
    if not rule_text:
        emit({"systemMessage": args.label + ": 규칙 원문(" + args.rules + ")을 읽지 못해 판정 없이 통과했습니다."})
        return 0
    rules = (INSTRUCTIONS.replace("{scope}", SCOPES[args.scope]).replace("{context}", str(CONTEXT))
             .replace("{label}", args.label).replace("{rules}", rule_text))

    verdict, error = judge(args, path, lines, rules)
    if error:
        emit({"systemMessage": args.label + "을 하지 못해 이 편집은 판정 없이 통과했습니다. 사유: " + error})
        return 0
    if verdict.get("verdict") != "deny":
        return 0

    added = "\n".join(lines)
    findings = [
        item for item in verdict.get("findings") or []
        if isinstance(item, dict) and str(item.get("quote") or "").strip()
        and str(item.get("quote")).strip() in added
    ]
    if not findings:
        return 0

    report = ["Deny: 이 규칙에 걸리는 표현입니다. (" + args.label + ", " + args.model + ", effort " + args.effort + ")"]
    for item in findings:
        report.append("- '" + str(item["quote"]).strip() + "': " + str(item.get("rule") or "").strip()
                      + " → " + str(item.get("fix") or "").strip())
    report.append("고쳐 쓴 뒤 다시 편집하십시오. 대상 파일: " + path)
    emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "\n".join(report),
        }
    })
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
