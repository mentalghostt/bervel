"""Keep the historical fixtures while routing Docker measurements to a service."""
import unittest

from workflows import build


class WorkflowTests(unittest.TestCase):
    def test_default_preserves_historical_loopback_and_version(self):
        workflow = build("1")
        request = workflow["nodes"][1]
        self.assertEqual(request["parameters"]["url"], "http://127.0.0.1:5689/record")
        self.assertIn("version: '1'", request["parameters"]["jsonBody"])

    def test_docker_observer_routes_all_http_nodes_without_changing_semantics(self):
        workflow = build("2", observer_url="http://observer:5689/")
        requests = [node for node in workflow["nodes"] if node["type"].endswith(".httpRequest")]
        self.assertEqual([node["parameters"]["url"] for node in requests], [
            "http://observer:5689/record", "http://observer:5689/effect",
            "http://observer:5689/record", "http://observer:5689/record",
        ])
        self.assertTrue(all("version: '2'" in node["parameters"]["jsonBody"] for node in requests))
        self.assertEqual(workflow["nodes"][4]["parameters"]["resume"], "webhook")


if __name__ == "__main__":
    unittest.main()
