"""Characterize approved manual recovery; never implement a replacement engine."""
import copy
import http.cookiejar
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import followup
import probe

probe.RESULT_FILE = probe.EXPERIMENT.parents[1] / "docs" / "quality" / "core-stage-2-n8n-manual-results.json"
probe.results = {"engine": "n8n", "engine_version": "2.43.5", "profile": "queue_manual_recovery",
                 "tested_on": "2026-10-14", "synthetic_data_only": True,
                 "product_implementation": False, "checks": [], "baselines": []}
recovered = {}


def compact(execution: dict) -> dict:
    return {key: execution.get(key) for key in
            ("id", "status", "finished", "retryOf", "retrySuccessId", "workflowId")}


def execution(execution_id: str) -> dict:
    return compact(followup.api("executions/" + execution_id))


def failed_list() -> list:
    filters = {"workflowId": "bervelCoreStage2", "status": ["error", "crashed"]}
    query = urllib.parse.urlencode({"limit": "100", "filter": json.dumps(filters)})
    return [compact(item) for item in followup.api("executions?" + query)["results"]]


def crash_baseline(label: str) -> tuple[str, str]:
    followup.activate("1")
    run_id = probe.begin(hold_seconds=45)
    probe.wait_for(lambda: probe.state(run_id)["effects"])
    execution_id = probe.events(run_id, "started")[0]["execution_id"]
    probe.stop("worker")
    probe.start("worker")
    probe.wait_for(lambda: execution(execution_id)["status"] == "crashed", seconds=120)
    snapshot = probe.state(run_id)
    assert len(snapshot["effects"]) == 1
    assert not probe.events(run_id, "waiting")
    assert not probe.events(run_id, "completed")
    probe.results["baselines"].append({"label": label, "run_id": run_id,
                                      "execution": execution(execution_id), "state": snapshot})
    probe.save()
    return run_id, execution_id


def finish_retry(run_id: str, response: dict) -> dict:
    child_id = response["id"]
    assert response["status"] == "waiting", "Retry did not reach review"
    waiting = probe.wait_for(lambda: [event for event in probe.events(run_id, "waiting")
                                     if event["execution_id"] == child_id])[0]
    assert not [event for event in probe.events(run_id, "completed")
                if event["execution_id"] == child_id], "Retry bypassed confirmation"
    probe.resume(waiting)
    probe.wait_for(lambda: execution(child_id)["status"] == "success")
    return execution(child_id)


def discover_and_recover():
    run_id, parent_id = crash_baseline("MR01")
    found = next(item for item in failed_list() if item["id"] == parent_id)
    assert found["status"] == "crashed"
    response = followup.api("executions/" + parent_id + "/retry", {"loadWorkflow": False})
    child = finish_retry(run_id, response)
    assert child["retryOf"] == parent_id
    snapshot = probe.state(run_id)
    assert len(snapshot["attempts"]) == 2
    assert len(snapshot["effects"]) == 1
    recovered.update(run_id=run_id, parent_id=parent_id, child_id=child["id"])
    return {"found": found, "child": child, "parent_after_retry": execution(parent_id),
            "run_id": run_id, "state": snapshot,
            "effect_deduplication_owner": "measurement_endpoint; NOT n8n"}


def duplicate_recovery():
    run_id, parent_id = recovered["run_id"], recovered["parent_id"]
    try:
        followup.api("executions/" + recovered["child_id"] + "/retry", {"loadWorkflow": False})
        successful_child_retry_status = 200
    except urllib.error.HTTPError as error:
        successful_child_retry_status = error.code
    assert successful_child_retry_status == 409, "Successful child could be retried"

    # Two separate sessions model two concurrent operator requests, without client serialization.
    barrier = threading.Barrier(2)

    def submit():
        jar = http.cookiejar.CookieJar()
        for cookie in followup.cookies:
            jar.set_cookie(copy.copy(cookie))
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        request = urllib.request.Request(
            "http://127.0.0.1:5688/rest/executions/" + parent_id + "/retry",
            data=b'{"loadWorkflow":false}', headers={"Content-Type": "application/json"},
        )
        barrier.wait(timeout=5)
        try:
            with client.open(request, timeout=90) as response:
                return {"http_status": response.status,
                        "execution": compact(json.loads(response.read())["data"])}
        except urllib.error.HTTPError as error:
            return {"http_status": error.code}

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))
    accepted = [item["execution"] for item in responses if item["http_status"] == 200]
    for child in accepted:
        assert child["retryOf"] == parent_id
        finish_retry(run_id, child)
    snapshot = probe.state(run_id)
    assert len(snapshot["effects"]) == 1
    assert len(snapshot["attempts"]) == 2 + len(accepted)
    return {"run_id": run_id, "parent_id": parent_id,
            "successful_child_retry_http_status": successful_child_retry_status,
            "concurrent_original_retry_responses": responses,
            "created_recovery_executions": len(accepted),
            "parent_after_retries": execution(parent_id), "state": snapshot,
            "effect_deduplication_owner": "measurement_endpoint; NOT n8n"}


def original_version_after_release():
    run_id, parent_id = crash_baseline("MR03")
    followup.activate("2")
    response = followup.api("executions/" + parent_id + "/retry", {"loadWorkflow": False})
    child = finish_retry(run_id, response)
    old_state = probe.state(run_id)
    old_completed = probe.events(run_id, "completed")
    assert [event["version"] for event in old_completed] == ["1"]
    assert len(old_state["effects"]) == 1

    new_run = probe.begin()
    waiting = probe.wait_for(lambda: probe.events(new_run, "waiting"))[0]
    probe.resume(waiting)
    probe.wait_for(lambda: execution(waiting["execution_id"])["status"] == "success")
    assert [event["version"] for event in probe.events(new_run, "completed")] == ["2"]
    return {"parent_id": parent_id, "child": child, "old_run": run_id,
            "old_state": old_state, "new_run": new_run, "new_state": probe.state(new_run),
            "published_version_id": followup.published_version()}


def changed_workflow_conflict():
    run_id, parent_id = crash_baseline("MR04")
    followup.activate("2")
    response = followup.api("executions/" + parent_id + "/retry", {"loadWorkflow": True})
    child = execution(response["id"])
    assert child["status"] == "error", "Changed payload under same key was not blocked"
    snapshot = probe.state(run_id)
    assert [item["version"] for item in snapshot["attempts"]] == ["1", "2"]
    assert [item["version"] for item in snapshot["effects"]] == ["1"]
    assert not probe.events(run_id, "waiting")
    assert not probe.events(run_id, "completed")
    return {"parent_id": parent_id, "child": child, "run_id": run_id,
            "state": snapshot, "conflict_detection_owner": "measurement_endpoint; NOT n8n"}


if __name__ == "__main__":
    try:
        probe.start("observer")
        probe.restart_engine()
        followup.login()
        probe.wait_for(lambda: probe.http("http://127.0.0.1:5689/")[0] == 200)
        probe.check("MR01_discover_crash_and_manual_recovery", discover_and_recover)
        if recovered:
            probe.check("MR02_concurrent_duplicate_recovery", duplicate_recovery)
        probe.check("MR03_original_version_after_release", original_version_after_release)
        probe.check("MR04_changed_workflow_conflict", changed_workflow_conflict)
    finally:
        for role in list(probe.children):
            probe.stop(role)
        for log in probe.log_files:
            log.close()
        probe.results["status"] = "probe_completed_engine_decision_pending"
        probe.save()
