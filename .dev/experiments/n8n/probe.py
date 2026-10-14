"""Run the approved throwaway n8n probe. Only its own processes are stopped."""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

from launch import EXPERIMENT, RUNTIME, command, environment

RESULT_FILE = EXPERIMENT.parents[1] / "docs" / "quality" / "core-stage-2-n8n-results.json"
results = {"engine": "n8n", "engine_version": "2.43.5", "profile": "queue",
           "synthetic_data_only": True, "checks": []}
children = {}
log_files = []
log_offsets = {}


def http(url: str, payload=None):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read()
        return response.status, json.loads(body) if body else None


def state(run_id: str) -> dict:
    return http("http://127.0.0.1:5689/state?run_id=" + run_id)[1]


def wait_for(condition, seconds=120):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for role, child in children.items():
            if child.poll() is not None:
                raise RuntimeError(role + " exited; read its log in .dev/local/core-stage-2")
        try:
            value = condition()
            if value:
                return value
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.25)
    raise TimeoutError("Expected state was not reached within " + str(seconds) + " seconds")


def events(run_id: str, phase: str) -> list:
    return [event for event in state(run_id)["events"] if event.get("phase") == phase]


def start(role: str):
    log = (RUNTIME / (role + ".log")).open("a", encoding="utf-8")
    log_files.append(log)
    log_offsets[role] = log.tell()
    arguments = ([sys.executable, str(EXPERIMENT / "observer.py"),
                  "--database", str(RUNTIME / "observer.sqlite")]
                 if role == "observer" else command("worker" if role == "worker" else "start"))
    children[role] = subprocess.Popen(
        arguments, cwd=EXPERIMENT, env=environment(role),
        stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    print("Started experimental " + role + " PID=" + str(children[role].pid), flush=True)


def stop(role: str):
    child = children.pop(role)
    if child.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        else:
            child.kill()
        child.wait(timeout=10)


def cli(*arguments):
    with (RUNTIME / "cli.log").open("a", encoding="utf-8") as log:
        subprocess.run(command(*arguments), cwd=EXPERIMENT, env=environment(),
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)


def restart_engine():
    for role in ("worker", "main"):
        if role in children:
            stop(role)
    for role in ("main", "worker"):
        start(role)
    def ready(role, message):
        with (RUNTIME / (role + ".log")).open(encoding="utf-8") as log:
            log.seek(log_offsets[role])
            return message in log.read()
    wait_for(lambda: ready("main", "Editor is now accessible via") and
             ready("worker", "n8n worker is now ready"))
    wait_for(lambda: http("http://127.0.0.1:5688/healthz/readiness")[0] == 200)


def begin(hold_seconds=0) -> str:
    run_id = "stage2-" + uuid.uuid4().hex
    wait_for(lambda: http("http://127.0.0.1:5688/webhook/bervel-core-stage-2",
                         {"run_id": run_id, "revision": "1", "hold_seconds": hold_seconds})[0] == 200)
    return run_id


def resume(event):
    return http(event["resume_url"], {"decision": "approved", "synthetic": True})[0]


def save():
    RESULT_FILE.write_text(json.dumps(results, indent=2), encoding="utf-8")


def check(name: str, operation):
    print("Running " + name, flush=True)
    started = time.monotonic()
    try:
        details = operation()
        result = {"id": name, "status": "pass", "details": details}
    except (AssertionError, TimeoutError, RuntimeError, urllib.error.URLError) as error:
        result = {"id": name, "status": "fail", "error": str(error) or type(error).__name__}
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    results["checks"].append(result)
    save()
    print(name + ": " + result["status"], flush=True)


def waiting_restart():
    run_id = begin()
    waiting = wait_for(lambda: events(run_id, "waiting"))[0]
    restart_engine()
    assert not events(run_id, "completed"), "Restart advanced the waiting execution"
    resume(waiting)
    completed = wait_for(lambda: events(run_id, "completed"))
    assert len(completed) == 1
    assert completed[0]["execution_id"] == waiting["execution_id"]
    return {"run_id": run_id, "state": state(run_id)}


def duplicate_commands():
    run_id = begin()
    first = wait_for(lambda: events(run_id, "waiting"))[0]
    resume(first)
    wait_for(lambda: events(run_id, "completed"))
    try:
        duplicate_status = resume(first)
    except urllib.error.HTTPError as error:
        duplicate_status = error.code
    assert len(events(run_id, "completed")) == 1, "Same resume published twice"
    http("http://127.0.0.1:5688/webhook/bervel-core-stage-2",
         {"run_id": run_id, "revision": "1", "hold_seconds": 0})
    waiting = wait_for(lambda: events(run_id, "waiting") if len(events(run_id, "waiting")) == 2 else None)
    resume(waiting[1])
    wait_for(lambda: events(run_id, "completed") if len(events(run_id, "completed")) == 2 else None)
    snapshot = state(run_id)
    assert len(snapshot["effects"]) == 1, "Measurement tool did not deduplicate effect"
    return {"run_id": run_id, "duplicate_resume_http_status": duplicate_status,
            "start_is_deduplicated_by_engine": len(events(run_id, "started")) == 1,
            "effect_deduplication_owner": "measurement_endpoint; NOT n8n",
            "state": snapshot}


def worker_crash():
    run_id = begin(hold_seconds=45)
    results["crash_run_id"] = run_id
    save()
    wait_for(lambda: state(run_id)["effects"])
    stop("worker")  # Kill only the Popen child after effect, before acknowledgement.
    start("worker")
    waiting = wait_for(lambda: events(run_id, "waiting"), seconds=150)[0]
    resume(waiting)
    wait_for(lambda: events(run_id, "completed"))
    snapshot = state(run_id)
    assert len(snapshot["effects"]) == 1
    return {"run_id": run_id, "state": snapshot}


def version_change():
    old_run = begin()
    waiting = wait_for(lambda: events(old_run, "waiting"))[0]
    for role in ("worker", "main"):
        stop(role)
    cli("import:workflow", "--input=" + str(EXPERIMENT / "workflow-v2.json"))
    cli("publish:workflow", "--id=bervelCoreStage2")
    restart_engine()
    resume(waiting)
    old_completed = wait_for(lambda: events(old_run, "completed"))
    assert old_completed[0]["version"] == "1", "Old execution used changed workflow semantics"
    new_run = begin()
    new_waiting = wait_for(lambda: events(new_run, "waiting"))[0]
    resume(new_waiting)
    new_completed = wait_for(lambda: events(new_run, "completed"))
    assert new_completed[0]["version"] == "2", "New execution did not use published v2"
    return {"old_run": old_run, "new_run": new_run,
            "old_state": state(old_run), "new_state": state(new_run)}


if __name__ == "__main__":
    try:
        cli("publish:workflow", "--id=bervelCoreStage2")
        start("observer")
        restart_engine()
        wait_for(lambda: http("http://127.0.0.1:5689/")[0] == 200)
        check("OR01_waiting_restart", waiting_restart)
        check("OR02_duplicate_commands", duplicate_commands)
        check("OR03_worker_crash_after_effect", worker_crash)
        check("OR04_workflow_version_change", version_change)
    finally:
        if "observer" in children and results.get("crash_run_id"):
            results["crash_state"] = state(results["crash_run_id"])
        for role in list(children):
            stop(role)
        for log in log_files:
            log.close()
        save()
