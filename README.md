# good-korean-claude

Claude가 쓰는 한국어에서 번역투를 덜어 내는 Claude Code 플러그인입니다. 글쓰기 스타일 '체계적 추론자 v4'와, 파일에 들어가기 직전의 한국어 문장을 그 스타일의 규칙과 대조하는 훅이 들어 있습니다.

## 설치

Claude Code에서 다음 두 명령을 차례로 실행합니다.

```
/plugin marketplace add 115dkk/good-korean-claude
/plugin install good-korean-claude@korean
```

## 들어 있는 것

### 스타일 `chegyejeog-chulonja-v4-style`

답변을 쓰기 전에 정리할 것, 한국어 문장 규칙, 형식, 분량과 구성, 창작물을 다루는 법을 정한 글쓰기 스타일입니다. 한국어 규칙에는 번역투 판정 기준, 물리·계량 은유, 문장 구조, 피동과 해석, 특정 용법, 어휘가 들어 있습니다.

이름을 대고 부를 때만 적용되고, 한 번 부르면 사용자가 다른 스타일을 요청할 때까지 대화 전체에 적용됩니다.

```
/good-korean-claude:chegyejeog-chulonja-v4-style
```

### 번역투 판정 훅

Write, Edit, MultiEdit가 파일을 쓰기 직전에 돌아갑니다. 편집이 새로 들이는 줄 가운데 한글이 든 줄을 모아, 사용자의 `claude` CLI로 Claude Sonnet 5.5(effort medium)를 불러 스타일 2장 '한국어'의 규칙과 대조하게 합니다.

- 규칙에 분명히 걸리는 표현이 있으면 편집을 거부합니다. 거부 사유에는 걸린 표현, 어긴 규칙, 고친 말이 들어 있고, Claude는 그것을 보고 고쳐 쓴 뒤 다시 편집합니다.
- 걸리는 표현이 없으면 편집이 그대로 진행됩니다. 한글이 없는 편집, `~/.claude`와 `~/.codex` 아래 파일, 잠금 파일은 모델을 부르지 않고 바로 통과합니다.
- 판정 모델을 부르지 못하면(PATH에 `claude`가 없거나, 시간이 초과되거나, 오류가 나면) 편집은 그대로 진행되고, 판정하지 못했다는 사실을 Claude에게 알립니다.

스타일을 부르지 않아도 플러그인이 켜져 있으면 훅은 늘 돌아갑니다. 판정할 때마다 모델을 한 번 부르므로 편집 한 번에 5~10초가 더 걸리고, 사용량은 쓰고 있는 Claude 요금제에서 나갑니다. `python3`와 `claude`가 PATH에 있어야 합니다.

## 라이선스

MIT입니다. [LICENSE](LICENSE)를 보십시오.
