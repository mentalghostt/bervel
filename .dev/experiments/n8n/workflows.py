"""Generate synthetic workflow fixtures, not application workflows."""
import json
import uuid
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent
WORKFLOW_ID = "bervelCoreStage2"


def build(version: str, observer_url: str = "http://127.0.0.1:5689") -> dict:
    source = "$('Start').first().json.body"
    identity = f"run_id: {source}.run_id, revision: {source}.revision, version: '{version}'"

    def request(name: str, endpoint: str, properties: str) -> dict:
        return {
            "id": name.lower().replace(" ", "-"), "name": name,
            "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
            "position": [0, 0],
            "parameters": {
                "method": "POST", "url": observer_url.rstrip("/") + "/" + endpoint,
                "sendBody": True, "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify({" + properties + "}) }}",
                "options": {"timeout": 120000},
            },
        }

    nodes = [
        {"id": "start", "name": "Start", "type": "n8n-nodes-base.webhook",
         "typeVersion": 2, "position": [0, 0], "webhookId": "bervel-stage-2-start",
         "parameters": {"httpMethod": "POST", "path": "bervel-core-stage-2",
                        "responseMode": "onReceived", "options": {}}},
        request("Started", "record", identity + ", phase: 'started', execution_id: $execution.id"),
        request("Publish", "effect", identity +
                f", key: {source}.run_id + ':' + {source}.revision, hold_seconds: {source}.hold_seconds || 0"),
        request("Waiting", "record", identity +
                ", phase: 'waiting', execution_id: $execution.id, resume_url: $execution.resumeUrl"),
        {"id": "wait", "name": "Wait", "type": "n8n-nodes-base.wait",
         "typeVersion": 1.1, "position": [0, 0], "webhookId": "bervel-stage-2-review",
         "parameters": {"resume": "webhook", "httpMethod": "POST",
                        "responseMode": "onReceived", "options": {}}},
        request("Completed", "record", identity + ", phase: 'completed', execution_id: $execution.id"),
    ]
    for index, node in enumerate(nodes):
        node["position"] = [index * 240, 0]
    return {
        "id": WORKFLOW_ID, "name": "BERVEL core stage 2 synthetic v" + version,
        "versionId": str(uuid.uuid5(uuid.NAMESPACE_URL, "bervel-stage-2-v" + version)),
        "active": False, "nodes": nodes,
        "connections": {
            previous["name"]: {"main": [[{"node": following["name"], "type": "main", "index": 0}]]}
            for previous, following in zip(nodes, nodes[1:])
        },
        "settings": {"executionOrder": "v1", "saveExecutionProgress": True},
    }


if __name__ == "__main__":
    for version in ("1", "2"):
        (DIRECTORY / ("workflow-v" + version + ".json")).write_text(
            json.dumps([build(version)], indent=2), encoding="utf-8"
        )
