"""Characterize native retry and publication via n8n's authenticated UI API."""
import http.cookiejar
import json
import os
import secrets
import subprocess
import urllib.request

import probe
from workflows import build

cookies = http.cookiejar.CookieJar()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))


def api(path, payload=None):
    request = urllib.request.Request(
        "http://127.0.0.1:5688/rest/" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with client.open(request, timeout=90) as response:
        return json.loads(response.read())["data"]


def login():
    password_file = probe.RUNTIME / "owner.env"
    fresh = not password_file.exists()
    if fresh:
        password_file.write_text("SPIKE_OWNER_PASSWORD=" + secrets.token_urlsafe(24) + "Aa1!", encoding="utf-8")
    password = password_file.read_text(encoding="utf-8").split("=", 1)[1]
    if fresh:
        api("owner/setup", {"email": "core-spike@example.invalid", "firstName": "Core",
                            "lastName": "Spike", "password": password})
    else:
        api("login", {"emailOrLdapLoginId": "core-spike@example.invalid", "password": password})


def sql_value(sql):
    result = subprocess.run(
        [os.environ["SPIKE_PSQL_PATH"], "-w", "-h", os.environ["SPIKE_PG_HOST"],
         "-U", "bervel_n8n_spike", "-d", "bervel_core_spike_n8n", "-A", "-t", "-c",
         sql],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def published_version():
    return sql_value('SELECT "publishedVersionId" FROM workflow_published_version WHERE "workflowId"=\'bervelCoreStage2\';')


def imported_version_id(version):
    # n8n assigns its own opaque version ID on import; the fixture UUID is not retained.
    rows = json.loads(sql_value('''SELECT json_agg(json_build_object(
        'version_id', h."versionId", 'body', n->'parameters'->>'jsonBody'))
        FROM workflow_history h, json_array_elements(h.nodes) n
        WHERE h."workflowId"='bervelCoreStage2' AND n->>'name'='Completed';'''))
    expected = build(version)["nodes"][-1]["parameters"]["jsonBody"]
    return next(row["version_id"] for row in rows if row["body"] == expected)


def activate(version):
    version_id = imported_version_id(version)
    api("workflows/bervelCoreStage2/activate", {"versionId": version_id})
    probe.wait_for(lambda: published_version() == version_id)


def manual_retry():
    run_id = probe.results["crash_run_id"]
    original = probe.state(run_id)
    execution_id = original["events"][0]["execution_id"]
    retry_response = api("executions/" + execution_id + "/retry", {"loadWorkflow": False})
    waiting = probe.wait_for(lambda: probe.events(run_id, "waiting"), seconds=120)[0]
    probe.resume(waiting)
    probe.wait_for(lambda: probe.events(run_id, "completed"))
    snapshot = probe.state(run_id)
    assert len(snapshot["effects"]) == 1
    assert len(snapshot["attempts"]) >= 2
    return {"original_execution_id": execution_id, "retry_response": retry_response,
            "run_id": run_id, "state": snapshot,
            "effect_deduplication_owner": "measurement_endpoint; NOT n8n"}


def api_version_change():
    activate("1")
    old_run = probe.begin()
    waiting = probe.wait_for(lambda: probe.events(old_run, "waiting"))[0]
    activate("2")
    probe.restart_engine()
    # Cookies and server signing keys survive restart; login verifies the session explicitly.
    login()
    probe.resume(waiting)
    old_completed = probe.wait_for(lambda: probe.events(old_run, "completed"))
    new_run = probe.begin()
    new_waiting = probe.wait_for(lambda: probe.events(new_run, "waiting"))[0]
    probe.resume(new_waiting)
    new_completed = probe.wait_for(lambda: probe.events(new_run, "completed"))
    details = {"old_run": old_run, "new_run": new_run,
               "old_state": probe.state(old_run), "new_state": probe.state(new_run),
               "published_version_id": published_version()}
    probe.results["api_version_change_state"] = details
    probe.save()
    assert old_completed[0]["version"] == "1", "Old execution changed its semantics"
    assert new_completed[0]["version"] == "2", "New execution did not use published v2"
    return details


if __name__ == "__main__":
    probe.results = json.loads(probe.RESULT_FILE.read_text(encoding="utf-8"))
    # Preserve previous diagnostic attempts separately, without counting corrected harness failures.
    ids = {"OR03B_native_manual_retry", "OR04B_authenticated_api_version_change"}
    earlier = [item for item in probe.results["checks"] if item["id"] in ids]
    probe.results.setdefault("diagnostic_attempts", []).extend(earlier)
    probe.results["checks"] = [item for item in probe.results["checks"] if item["id"] not in ids]
    try:
        probe.start("observer")
        probe.restart_engine()
        login()
        probe.check("OR03B_native_manual_retry", manual_retry)
        probe.check("OR04B_authenticated_api_version_change", api_version_change)
    finally:
        if "observer" in probe.children:
            probe.results["crash_state_after_manual_retry"] = probe.state(probe.results["crash_run_id"])
        for role in list(probe.children):
            probe.stop(role)
        for log in probe.log_files:
            log.close()
        probe.save()
