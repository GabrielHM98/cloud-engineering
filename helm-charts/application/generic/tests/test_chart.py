"""Offline Helm rendering and Kubernetes schema checks; no cluster is used."""
import copy
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml
from kubernetes_validate import validate

CHART = Path(__file__).resolve().parents[1]
HELM = os.environ.get("HELM", "helm")
BASE = {"image": {"repository": "example/app", "tag": "test"}}

def render(overrides=None, *, route=False, chart=CHART, base=True):
    values = copy.deepcopy(BASE if base else {})
    def merge(a, b):
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                merge(a[k], v)
            else:
                a[k] = v
    merge(values, overrides or {})
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "values.yaml"
        path.write_text(yaml.safe_dump(values))
        cmd = [HELM, "template", "test", str(chart), "-n", "test-ns", "-f", str(path)]
        if route:
            cmd += ["--api-versions", "route.openshift.io/v1"]
        result = subprocess.run(cmd, text=True, capture_output=True)
    return result

def docs(overrides=None, **kwargs):
    result = render(overrides, **kwargs)
    if result.returncode:
        raise AssertionError(result.stderr)
    objects = [d for d in yaml.safe_load_all(result.stdout) if d]
    for obj in objects:
        if obj["kind"] != "Route":
            validate(obj, desired_version="1.31.0", strict=True)
    return objects

def by_kind(objects, kind):
    return next(o for o in objects if o["kind"] == kind)

def pod(objects):
    return by_kind(objects, "Deployment")["spec"]["template"]["spec"]

class ChartTests(unittest.TestCase):
    def test_minimal_resources(self):
        self.assertEqual({o["kind"] for o in docs()}, {"Deployment", "Service", "ServiceAccount"})

    def test_selectors_match(self):
        objects = docs()
        deployment = by_kind(objects, "Deployment")
        selector = deployment["spec"]["selector"]["matchLabels"]
        self.assertEqual(selector, deployment["spec"]["template"]["metadata"]["labels"])
        self.assertEqual(selector, by_kind(objects, "Service")["spec"]["selector"])

    def test_digest_precedence(self):
        c = pod(docs({"image": {"digest": "sha256:" + "a" * 64}}))["containers"][0]
        self.assertEqual(c["image"], "example/app@sha256:" + "a" * 64)

    def test_image_required(self):
        self.assertNotEqual(render(base=False).returncode, 0)

    def test_tag_required(self):
        self.assertNotEqual(render({"image": {"tag": ""}}).returncode, 0)

    def test_invalid_digest(self):
        self.assertNotEqual(render({"image": {"digest": "sha256:bad"}}).returncode, 0)

    def test_unknown_value(self):
        self.assertNotEqual(render({"repilcaCount": 3}).returncode, 0)

    def test_port_validation(self):
        self.assertNotEqual(render({"containerPort": 80}).returncode, 0)

    def test_security_defaults(self):
        spec = pod(docs())
        self.assertTrue(spec["securityContext"]["runAsNonRoot"])
        self.assertNotIn("runAsUser", spec["securityContext"])
        self.assertNotIn("fsGroup", spec["securityContext"])
        self.assertFalse(spec["automountServiceAccountToken"])
        c = spec["containers"][0]["securityContext"]
        self.assertFalse(c["allowPrivilegeEscalation"])
        self.assertTrue(c["readOnlyRootFilesystem"])
        self.assertEqual(c["capabilities"]["drop"], ["ALL"])

    def test_readiness_liveness_and_startup(self):
        c = pod(docs())["containers"][0]
        self.assertEqual(c["readinessProbe"]["httpGet"]["path"], "/readyz")
        self.assertEqual(c["livenessProbe"]["httpGet"]["path"], "/healthz")
        self.assertIn("startupProbe", c)

    def test_disable_probe(self):
        self.assertNotIn("livenessProbe", pod(docs({"probes": {"liveness": {"enabled": False}}}))["containers"][0])

    def test_config_rollout(self):
        def annotation(v):
            return by_kind(docs({"configMap": {"enabled": True, "data": {"MODE": v}}}), "Deployment")["spec"]["template"]["metadata"]["annotations"]["checksum/config"]
        self.assertNotEqual(annotation("one"), annotation("two"))

    def test_config_must_be_strings(self):
        self.assertNotEqual(render({"configMap": {"enabled": True, "data": {"PORT": 8080}}}).returncode, 0)

    def test_secret_reference(self):
        env = [{"name": "TOKEN", "valueFrom": {"secretKeyRef": {"name": "credentials", "key": "token"}}}]
        objects = docs({"env": env})
        self.assertEqual(pod(objects)["containers"][0]["env"], env)
        self.assertNotIn("Secret", [o["kind"] for o in objects])

    def test_existing_service_account(self):
        objects = docs({"serviceAccount": {"create": False, "name": "existing"}})
        self.assertNotIn("ServiceAccount", [o["kind"] for o in objects])
        self.assertEqual(pod(objects)["serviceAccountName"], "existing")
        self.assertNotEqual(render({"serviceAccount": {"create": False}}).returncode, 0)

    def test_hpa_owns_replicas(self):
        objects = docs({"autoscaling": {"enabled": True}})
        self.assertNotIn("replicas", by_kind(objects, "Deployment")["spec"])
        self.assertEqual(by_kind(objects, "HorizontalPodAutoscaler")["spec"]["minReplicas"], 2)

    def test_invalid_hpa_range(self):
        self.assertNotEqual(render({"autoscaling": {"enabled": True, "minReplicas": 6, "maxReplicas": 2}}).returncode, 0)

    def test_hpa_cpu_request(self):
        self.assertNotEqual(render({"autoscaling": {"enabled": True}, "resources": {"requests": {"cpu": None}}}).returncode, 0)

    def test_recreate(self):
        self.assertEqual(by_kind(docs({"strategy": {"type": "Recreate"}}), "Deployment")["spec"]["strategy"], {"type": "Recreate"})

    def test_ingress(self):
        objects = docs({"exposure": {"type": "ingress", "host": "demo.example.com", "ingress": {"className": "nginx", "tls": [{"secretName": "tls", "hosts": ["demo.example.com"]}]}}})
        self.assertIn("Ingress", [o["kind"] for o in objects])
        self.assertNotIn("Route", [o["kind"] for o in objects])

    def test_route_api_guard(self):
        v = {"exposure": {"type": "route", "host": "demo.apps.example.com"}}
        self.assertNotEqual(render(v).returncode, 0)
        obj = by_kind(docs(v, route=True), "Route")
        self.assertEqual(obj["spec"]["tls"], {"termination": "edge", "insecureEdgeTerminationPolicy": "Redirect"})
        self.assertEqual(obj["spec"]["to"]["name"], "test-web-service")

    def test_exposure_host_required(self):
        self.assertNotEqual(render({"exposure": {"type": "ingress"}}).returncode, 0)

    def test_network_deny(self):
        obj = by_kind(docs({"networkPolicy": {"enabled": True}}), "NetworkPolicy")
        self.assertEqual(obj["spec"]["ingress"], [])
        self.assertEqual(obj["spec"]["policyTypes"], ["Ingress"])

    def test_network_peer_and_custom_port(self):
        peers = [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "edge"}}, "podSelector": {"matchLabels": {"app": "proxy"}}}]
        obj = by_kind(docs({"containerPort": 9000, "networkPolicy": {"enabled": True, "ingressPeers": peers}}), "NetworkPolicy")
        self.assertEqual(obj["spec"]["ingress"][0]["from"], peers)
        self.assertEqual(obj["spec"]["ingress"][0]["ports"][0]["port"], 9000)

    def test_pdb(self):
        self.assertEqual(by_kind(docs({"podDisruptionBudget": {"enabled": True}}), "PodDisruptionBudget")["spec"]["minAvailable"], 1)

    def test_volumes_and_scheduling(self):
        objects = docs({"extraVolumes": [{"name": "config", "configMap": {"name": "external"}}], "extraVolumeMounts": [{"name": "config", "mountPath": "/config", "readOnly": True}], "nodeSelector": {"workload": "apps"}})
        self.assertEqual(len(pod(objects)["volumes"]), 2)
        self.assertEqual(pod(objects)["nodeSelector"], {"workload": "apps"})

    def test_disable_tmp(self):
        spec = pod(docs({"tmp": {"enabled": False}}))
        self.assertNotIn("volumes", spec)
        self.assertNotIn("volumeMounts", spec["containers"][0])

    def test_all_examples(self):
        for path in (CHART / "examples").glob("*.yaml"):
            with self.subTest(file=path.name):
                docs(yaml.safe_load(path.read_text()), route=path.name == "openshift-route.yaml")

    def test_umbrella_alias_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            parent = Path(d)
            (parent / "Chart.yaml").write_text(yaml.safe_dump({"apiVersion": "v2", "name": "suite", "version": "0.1.0", "dependencies": [{"name": "web-service", "version": "0.1.0", "repository": "file://unused", "alias": alias} for alias in ["web", "catalog", "predictor"]]}))
            shutil.copytree(CHART, parent / "charts" / "web-service")
            objects = docs({alias: BASE for alias in ["web", "catalog", "predictor"]}, chart=parent, base=False)
            deployments = [o for o in objects if o["kind"] == "Deployment"]
            self.assertEqual(len(deployments), 3)
            selectors = [o["spec"]["selector"]["matchLabels"] for o in deployments]
            self.assertEqual(len({tuple(sorted(x.items())) for x in selectors}), 3)
            self.assertEqual(len({o["metadata"]["name"] for o in deployments}), 3)

if __name__ == "__main__":
    unittest.main(verbosity=2)
