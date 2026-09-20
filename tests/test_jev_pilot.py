"""Keep the experimental benchmark isolated, reproducible and honestly scored."""

import json
import sqlite3

import pytest

from experiments.jev_bookmarks import pilot


@pytest.fixture
def sample(tmp_path):
    db = tmp_path / "source.sqlite3"
    connection = sqlite3.connect(db)
    connection.executescript("""
        CREATE TABLE jobs (id INTEGER, bookmark_id TEXT, input_revision TEXT,
                           input_json TEXT, state TEXT);
        CREATE TABLE receipts (id INTEGER, job_id INTEGER, effect_kind TEXT,
                               payload_json TEXT, created_at TEXT);
        CREATE TABLE decisions (bookmark_id TEXT, action TEXT, created_at TEXT);
    """)
    for index, (bookmark_id, kind, text) in enumerate(
        [
            ("1", "bookmarks", "Old version"),
            ("1", "bookmarks", "New version"),
            ("2", "likes", "Excluded like"),
            ("3", "bookmarks", "Português: ferramenta para organizar notas"),
        ]
    ):
        connection.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?)",
            (
                index,
                bookmark_id,
                str(index),
                json.dumps(
                    {
                        "kind": kind,
                        "text": text,
                        "author": {"username": "example"},
                        "private_context": "PRIVATE WORKSPACE DATA",
                    }
                ),
                "done",
            ),
        )
        connection.execute(
            "INSERT INTO receipts VALUES (?,?,?,?,?)",
            (
                index,
                index,
                "quick",
                json.dumps(
                    {
                        "status": "succeeded",
                        "model": "historical",
                        "output": {"summary": "HISTORICAL ANSWER MUST NOT LEAK"},
                    }
                ),
                str(index),
            ),
        )
    connection.execute("INSERT INTO decisions VALUES ('1','act','2026-09-19')")
    connection.commit()
    connection.close()
    before = db.read_bytes()
    out = tmp_path / "run"
    manifest = pilot.prepare(db, out)
    assert db.read_bytes() == before
    return out, manifest


def test_sample_deduplicates_and_excludes_likes_and_reference_leakage(sample):
    out, manifest = sample
    assert manifest["historical_triages"] == 4
    assert manifest["unique_bookmarks"] == 2
    rows, rubric = pilot.load_sample(out)
    assert {r["id"] for r in rows} == {"1", "3"}
    row = next(r for r in rows if r["id"] == "1")
    assert row["state"]["text"] == "New version"
    for request in [pilot.request_for(row, rubric), pilot.baseline_request(row, rubric)]:
        serialized = json.dumps(request)
        assert "PRIVATE WORKSPACE DATA" not in serialized
        assert "HISTORICAL ANSWER" not in serialized
        assert "historical_reference" not in serialized
        assert "human-decisions" not in serialized


def test_frozen_inputs_reject_mutation(sample):
    out, _ = sample
    (out / "rubric.json").write_text("{}")
    with pytest.raises(ValueError, match="Rubric changed"):
        pilot.load_sample(out)


def test_missing_key_does_not_create_attempt(sample, monkeypatch):
    out, _ = sample
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="missing"):
        pilot.run(out, "jev", None, "all", 0.10)
    assert not (out / "jev.jsonl").exists()


def test_budget_reserves_maximum_before_call(sample, monkeypatch):
    out, _ = sample
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    monkeypatch.setattr(pilot, "jev_call", lambda *a: pytest.fail("No budget for a request"))
    pilot.run(out, "jev", None, "all", pilot.MAX_CALL_COST / 2)
    assert not (out / "jev.jsonl").exists()


def valid_response(rubric):
    topics = rubric["questions"]["topic"]["criteria"]
    return {
        "model": pilot.MODEL,
        "usage": {"input_tokens": 100},
        "answers": {
            "topic": {
                "type": "choice",
                "choice": "ai_software",
                "confidence": 1,
                "probabilities": {k: int(k == "ai_software") for k in topics},
            },
            "priority": {
                "type": "score",
                "score": 2.4,
                "confidence": 0.6,
                "probabilities": {"0": 0, "1": 0, "2": 0.6, "3": 0.4},
            },
            "needs_context": {"type": "noul", "noul": 0.7},
        },
    }


def test_resume_does_not_repeat_success_or_count_reservation_twice(sample, monkeypatch):
    out, _ = sample
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    calls = []

    def call(row, rubric, key):
        calls.append(row["id"])
        return valid_response(rubric)

    monkeypatch.setattr(pilot, "jev_call", call)
    pilot.run(out, "jev", 1, "all", 0.10)
    pilot.run(out, "jev", None, "all", 0.10)
    pilot.run(out, "jev", None, "all", 0.10)
    assert len(calls) == len(set(calls)) == 2
    result = pilot.report(out)
    assert result["jev"]["reserved_or_billed_usd"] == pytest.approx(200 * pilot.PRICE)
    assert result["status"] == "awaiting_paired_inference"


def test_interrupted_paid_attempt_is_reserved_and_not_retried(sample, monkeypatch):
    out, _ = sample
    rows, rubric = pilot.load_sample(out)
    pilot.append(
        out / "jev.jsonl",
        {
            "id": rows[0]["id"],
            "status": "started",
            "cost_usd": pilot.MAX_CALL_COST,
            "state_sha256": rows[0]["state_sha256"],
            "rubric_sha256": pilot.fingerprint(rubric),
        },
    )
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    monkeypatch.setattr(pilot, "jev_call", lambda *a: pytest.fail("Uncertain charge retained"))
    pilot.run(out, "jev", None, "all", pilot.MAX_CALL_COST)
    assert len(pilot.read_lines(out / "jev.jsonl")) == 1


def test_invalid_response_does_not_become_success(sample, monkeypatch):
    out, _ = sample
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    monkeypatch.setattr(pilot, "jev_call", lambda *a: {"usage": {"input_tokens": 100}})
    with pytest.raises(ValueError, match="failed request"):
        pilot.run(out, "jev", 1, "all", 0.10)
    record = pilot.read_lines(out / "jev.jsonl")[-1]
    assert record["status"] == "failed"
    assert record["cost_usd"] == pytest.approx(100 * pilot.PRICE)


def test_paired_metrics_include_missed_important_items():
    def receipt(topic, priority, context, elapsed):
        return {
            "prediction": {"topic": topic, "priority": priority, "needs_context": context},
            "elapsed_seconds": elapsed,
        }

    pairs = [
        ("a", receipt("ai", 3, False, 8), receipt("ai", 1, True, 0.2)),
        ("b", receipt("life", 0, True, 4), receipt("ai", 0, True, 0.4)),
    ]
    result = pilot.paired_metrics(pairs)
    assert result["topic_agreement"] == 0.5
    assert result["needs_context_agreement"] == 0.5
    assert result["baseline_important_recall"] == 0
    assert result["jev_below_priority_two_ids"] == ["a"]
    assert result["priority_mean_absolute_difference"] == 1
    assert pilot.paired_metrics([]) == {"n": 0}


def test_modal_level_keeps_priority_two_when_expected_score_is_below_two():
    baseline = {
        "prediction": {"topic": "ai", "priority": 2, "needs_context": True},
        "elapsed_seconds": 5,
    }
    jev = {
        "prediction": {"topic": "ai", "priority": 1.75, "needs_context": True},
        "elapsed_seconds": 0.7,
        "response": {
            "answers": {"priority": {"probabilities": {"0": 0.07, "1": 0.15, "2": 0.74, "3": 0.04}}}
        },
    }
    metrics = pilot.paired_metrics([("a", baseline, jev)])
    assert metrics["baseline_important_recall"] == 0
    assert metrics["modal_priority_agreement"] == 1
    assert metrics["modal_important_recall"] == 1
    assert metrics["modal_missed_important_ids"] == []


def test_modal_tie_uses_lower_level_without_looking_at_baseline():
    record = {
        "response": {
            "answers": {"priority": {"probabilities": {"0": 0, "1": 0.5, "2": 0.5, "3": 0}}}
        }
    }
    assert pilot.priority_level(record) == 1


def test_report_lists_modal_disagreement_even_with_small_difference_in_mean(sample, monkeypatch):
    out, _ = sample
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")

    def response(row, rubric, key):
        body = valid_response(rubric)
        body["answers"]["priority"].update(
            {
                "score": 1.37,
                "probabilities": {"0": 0.07, "1": 0.5, "2": 0.42, "3": 0.01},
            }
        )
        return body

    monkeypatch.setattr(pilot, "jev_call", response)
    monkeypatch.setattr(
        pilot,
        "baseline_call",
        lambda *a: {
            "model": "test",
            "prediction": {
                "topic": "ai_software",
                "priority": 2,
                "needs_context": True,
            },
        },
    )
    pilot.run(out, "baseline", None, "all", 0.10)
    pilot.run(out, "jev", None, "all", 0.10)
    result = pilot.report(out)
    assert len(result["disagreements"]) == 2
    assert all(r["jev_priority_level"] == 1 for r in result["disagreements"])
