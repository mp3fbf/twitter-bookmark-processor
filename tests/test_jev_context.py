import json

import pytest

from experiments.jev_context import pilot


@pytest.fixture
def experiment(tmp_path):
    source = {
        "source_id": "123",
        "kind": "paired",
        "state": {
            "reading_goal": "What color is the release badge?",
            "post": {
                "text": "The release is live. The badge is blue. More news soon.",
                "author": "example",
                "media_types": [],
                "url": "https://x.com/example/status/123",
            },
            "linked_url": "",
            "retrieved_text": "",
        },
        "evidence_field": "post.text",
        "evidence_span": "The badge is blue.",
        "answer_anchor": "blue",
        "missing_route": "fetch_post",
        "private_annotation": "PRIVATE REFERENCE MUST NEVER LEAK",
    }
    annotations = tmp_path / "annotations.json"
    annotations.write_text(json.dumps([source]))
    out = tmp_path / "data"
    pilot.prepare(annotations, out)
    return out, source


def test_deleted_evidence_and_gold_never_leak_to_model(experiment):
    out, _ = experiment
    rows, rubric, _ = pilot.load(out)
    removed = next(x for x in rows if x["variant"] == "removed")
    request = pilot.request_for(removed, rubric)
    assert "blue" not in json.dumps(request)
    assert "PRIVATE REFERENCE" not in json.dumps(request)
    assert "acceptable_routes" not in json.dumps(request)
    assert "variant" not in request["state"]


def test_repeated_answer_blocks_invalid_deletion(experiment, tmp_path):
    _, source = experiment
    source["state"]["post"]["text"] += " I like blue."
    path = tmp_path / "repeated.json"
    path.write_text(json.dumps([source]))
    with pytest.raises(ValueError, match="remains visible"):
        pilot.prepare(path, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_private_payload_field_is_rejected(experiment):
    _, source = experiment
    source["state"]["private_context"] = "secret"
    with pytest.raises(ValueError, match="Unexpected state field"):
        pilot.public_state(source["state"])


def test_frozen_reference_and_inputs_cannot_change(experiment):
    out, _ = experiment
    rows = pilot.read_lines(out / "dataset.jsonl")
    rows[0]["acceptable_routes"] = ["unresolved"]
    (out / "dataset.jsonl").write_text("\n".join(pilot.encode(x) for x in rows))
    with pytest.raises(ValueError, match="Frozen dataset changed"):
        pilot.load(out)


def test_budget_and_interrupted_reservation_prevent_extra_call(experiment, monkeypatch):
    out, _ = experiment
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    monkeypatch.setattr(pilot, "jev_call", lambda *args: pytest.fail("No paid call expected"))
    pilot.run(out, "jev", budget=pilot.MAX_CALL_COST / 2)
    assert not (out / "jev.jsonl").exists()
    rows, rubric, _ = pilot.load(out)
    for r in rows:
        pilot.append(
            out / "jev.jsonl",
            {
                "id": r["id"],
                "status": "started",
                "state_sha256": r["state_sha256"],
                "rubric_sha256": pilot.fingerprint(rubric),
                "cost_usd": pilot.MAX_CALL_COST,
            },
        )
    pilot.run(out, "jev")
    assert (
        pilot.report(out)["providers"]["jev"]["reserved_or_billed_usd"] == 2 * pilot.MAX_CALL_COST
    )


def test_missing_key_leaves_no_attempt(experiment, monkeypatch):
    out, _ = experiment
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="missing"):
        pilot.run(out, "jev")
    assert not (out / "jev.jsonl").exists()


def test_metrics_distinguish_false_ready_from_wrong_retrieval_route(experiment, monkeypatch):
    out, _ = experiment
    monkeypatch.setattr(
        pilot, "baseline_call", lambda *args: {"prediction": "ready", "model": "test"}
    )
    pilot.run(out, "baseline")
    r = pilot.report(out)["providers"]["baseline"]
    assert len(r["removed"]["false_ready_ids"]) == 1
    assert r["sufficient"]["unnecessary_retrieval_ids"] == []
    assert r["pairs_correct"] == 0
    before = (out / "baseline.jsonl").read_bytes()
    pilot.run(out, "baseline")
    assert (out / "baseline.jsonl").read_bytes() == before


def test_failure_is_not_retried_or_secret_logged(experiment, monkeypatch):
    out, _ = experiment
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-secret")

    def fail(*args):
        raise RuntimeError("test-secret private provider body")

    monkeypatch.setattr(pilot, "jev_call", fail)
    with pytest.raises(ValueError, match="Stopped"):
        pilot.run(out, "jev", limit=1)
    log = (out / "jev.jsonl").read_text()
    assert "test-secret" not in log and "provider body" not in log
    assert pilot.read_lines(out / "jev.jsonl")[-1]["cost_usd"] == pilot.MAX_CALL_COST


def test_model_and_probability_contract_are_validated():
    body = {
        "model": pilot.MODEL,
        "answers": {
            "next_action": {
                "type": "choice",
                "choice": "ready",
                "confidence": 1,
                "probabilities": {x: int(x == "ready") for x in pilot.ROUTES},
            }
        },
    }
    assert pilot.normalize(body) == "ready"
    body["model"] = "unreviewed-latest"
    with pytest.raises(ValueError, match="Pinned model"):
        pilot.normalize(body)


def test_wrong_destination_can_still_detect_missing_evidence(experiment, monkeypatch):
    out, _ = experiment
    monkeypatch.setattr(
        pilot,
        "baseline_call",
        lambda row, rubric: {
            "model": "test",
            "prediction": "ready" if row["variant"] == "sufficient" else "inspect_media",
        },
    )
    pilot.run(out, "baseline")
    result = pilot.report(out)["providers"]["baseline"]
    assert result["pairs_correct"] == 0
    assert result["pairs_sufficiency_correct"] == 1
    assert result["removed"]["false_ready_ids"] == []
