from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from wellfound.apply_probe import (  # noqa: E402
    classify_external_from_url,
    classify_form_snapshot,
    looks_eligibility_blocked,
)
from wellfound.apply_flow import extract_job_description_text  # noqa: E402


def test_cover_letter_only() -> None:
    result = classify_form_snapshot(
        textareas=["Cover letter"],
        inputs=[],
        page_text="Apply to Acme",
    )
    assert result.apply_kind == "cover_letter_only"


def test_cover_letter_unnamed_textarea() -> None:
    result = classify_form_snapshot(textareas=[""], inputs=[], page_text="")
    assert result.apply_kind == "cover_letter_only"


def test_has_questions_radio() -> None:
    result = classify_form_snapshot(
        textareas=["Message"],
        inputs=[{"tag": "input", "type": "radio", "label": "Years of experience"}],
        page_text="",
    )
    assert result.apply_kind == "has_questions"
    assert "Years of experience" in result.question_labels


def test_has_questions_select() -> None:
    result = classify_form_snapshot(
        textareas=["Note"],
        inputs=[{"tag": "select", "type": "select", "label": "Authorized to work?"}],
        page_text="",
    )
    assert result.apply_kind == "has_questions"


def test_eligibility_blocked() -> None:
    result = classify_form_snapshot(
        textareas=["Cover letter"],
        inputs=[],
        page_text="Sorry, you are not eligible for this role. Must be located in the US.",
    )
    assert result.apply_kind == "eligibility_blocked"
    assert looks_eligibility_blocked("outside of our hiring locations")


def test_external_url() -> None:
    assert classify_external_from_url("https://boards.greenhouse.io/acme/jobs/1")
    assert classify_external_from_url("https://jobs.lever.co/acme/abc")
    assert not classify_external_from_url("https://wellfound.com/jobs/123")
    assert not classify_external_from_url("https://angel.co/company/acme/jobs/1")


def test_unknown_empty_form() -> None:
    result = classify_form_snapshot(textareas=[], inputs=[], page_text="Job details")
    assert result.apply_kind == "unknown"


def test_custom_question_answer_name_as_cover() -> None:
    result = classify_form_snapshot(
        textareas=["cover letter"],
        inputs=[],
        page_text="",
    )
    assert result.apply_kind == "cover_letter_only"


def test_extract_job_description_text() -> None:
    html = """
    <div class="mt-6 rounded-xl border"><h2>About the job</h2>
    <div id="job-description"><p>Fully <strong>remote</strong> Python APIs at Acme.</p></div>
    </div>
    """
    text = extract_job_description_text(html)
    assert "remote" in text.lower()
    assert "Python APIs" in text or "python apis" in text.lower()


if __name__ == "__main__":
    test_cover_letter_only()
    test_cover_letter_unnamed_textarea()
    test_has_questions_radio()
    test_has_questions_select()
    test_eligibility_blocked()
    test_external_url()
    test_unknown_empty_form()
    test_custom_question_answer_name_as_cover()
    test_extract_job_description_text()
    print("apply_probe checks passed")
