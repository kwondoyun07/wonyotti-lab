"""Jev(TypeSafe) 판단층: 텍스트 판단만 맡긴다.

원칙 (TypeSafe 공식 문서의 jev-1.13 약점 목록 기준):
- 숫자 계산, 날짜 비교, 캔들 원자료는 넣지 않는다. 코드가 계산해 구간이나 문장으로 바꾼 것만 넣는다.
- 질문 하나에 판단 하나. 여러 요소는 질문을 나눠 한 요청에 묶고 코드에서 합친다.
- 임계값(threshold)은 출발점일 뿐. 내 로그로 확률과 실제 적중률을 대조해 보정한 뒤 쓴다.
- 한국어 메모에 대한 판단 품질은 따로 확인이 필요하다.

    python -m src.jev_layer --headline "Fed holds rates steady, signals two cuts this year"
    python -m src.jev_layer --note "어제 잃은 거 오늘 안에 무조건 복구한다"
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

EVENT_CATEGORIES = {
    "rates": "US interest-rate decisions or central bank policy",
    "macro_data": "Scheduled economic data releases such as inflation (CPI) or employment",
    "exchange_risk": "A crypto exchange outage, hack, insolvency, or withdrawal halt",
    "regulation": "Government regulation, lawsuits, or enforcement involving crypto",
    "other": "None of the above",
}


def news_questions() -> dict:
    """헤드라인 한 줄에 대한 질문. state 예: {"headline": "..."}"""
    from typesafe_sdk import Choice, Noul

    return {
        "us_macro_event": Noul(
            instructions=(
                "Is `headline` about a US interest-rate decision or a scheduled major "
                "US economic data release such as CPI or jobs data?"
            ),
        ),
        "event_type": Choice(
            instructions="Which category best describes `headline`?",
            criteria=EVENT_CATEGORIES,
        ),
    }


def tilt_questions() -> dict:
    """매매 전 한 줄 메모에 대한 질문. state 예: {"note": "..."}"""
    from typesafe_sdk import Noul

    return {
        "revenge": Noul(
            instructions="Does `note` express urgency to win back recent trading losses quickly?",
        ),
        "fomo": Noul(
            instructions="Does `note` express fear of missing a price move that has already happened?",
        ),
    }


def ask(state: dict, questions: dict, model: str | None = None) -> dict[str, Any]:
    """state와 질문들을 한 번의 요청으로 보내고 답을 파이썬 기본형으로 돌려준다."""
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise SystemExit("TYPESAFE_API_KEY가 없습니다. .env에 키를 넣어주세요.")
    from typesafe_sdk import TypeSafeClient

    with TypeSafeClient() as client:
        response = client.system_one(state=state, questions=questions, model=model)

    answers: dict[str, Any] = {}
    for qid in questions:
        a = response.answers[qid]
        answers[qid] = {
            k: getattr(a, k)
            for k in ("noul", "choice", "score", "confidence", "probabilities")
            if getattr(a, k, None) is not None
        }
    return answers


def tilt_lock(answers: dict, threshold: float = 0.7) -> bool:
    """복구 조급함이나 FOMO 확률이 임계값 이상이면 True → 그날 위험을 늘리는 주문 금지."""
    return any(answers.get(k, {}).get("noul", 0.0) >= threshold for k in ("revenge", "fomo"))


def macro_event(answers: dict, threshold: float = 0.7) -> bool:
    """미국 금리 · 주요 지표 관련 헤드라인이면 True → 발표 전후 신규 진입 조절용 신호."""
    return answers.get("us_macro_event", {}).get("noul", 0.0) >= threshold


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--headline", help="뉴스 헤드라인 한 줄")
    ap.add_argument("--note", help="매매 전 한 줄 메모")
    ap.add_argument("--model", default=None, help="예: jev-1.13 (기본: jev-latest)")
    args = ap.parse_args()
    if not (args.headline or args.note):
        ap.error("--headline 또는 --note 중 하나는 필요합니다")

    if args.headline:
        ans = ask({"headline": args.headline}, news_questions(), args.model)
        print(json.dumps(ans, ensure_ascii=False, indent=2, default=str))
        print("macro_event:", macro_event(ans))
    if args.note:
        ans = ask({"note": args.note}, tilt_questions(), args.model)
        print(json.dumps(ans, ensure_ascii=False, indent=2, default=str))
        print("tilt_lock:", tilt_lock(ans))


if __name__ == "__main__":
    main()
