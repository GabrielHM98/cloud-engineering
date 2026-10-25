"""Keep the app manifest consistent with its actual Docker/HTTP interfaces."""
from pathlib import Path
import unittest
import yaml

ROOT=Path(__file__).resolve().parents[1]


class AppContractTests(unittest.TestCase):
    def setUp(self):self.config=yaml.safe_load((ROOT/'app.yaml').read_text())

    def test_manifest_names_existing_targets_and_readiness_routes(self):
        self.assertEqual(self.config['schemaVersion'],1)
        self.assertEqual(set(self.config['components']),{'web','catalog','predictor'})
        dockerfile=(ROOT/'Dockerfile').read_text()
        for role,component in self.config['components'].items():
            self.assertIn(' AS '+component['target']+'\n',dockerfile)
            self.assertEqual(component['port'],8080)
            self.assertEqual(component['readyPath'],'/readyz')
            self.assertIn("'/readyz'",(ROOT/'services'/(role+'_api.py')).read_text())

    def test_internal_urls_use_the_declared_component_aliases(self):
        env=self.config['components']['web']['env']
        self.assertEqual(env['CATALOG_URL'],'http://catalog:8080')
        self.assertEqual(env['PREDICTOR_URL'],'http://predictor:8080')

    def test_preparation_verifies_real_runtime_before_checks(self):
        prepare=self.config['prepare']
        self.assertEqual(prepare[0],['python3','scripts/runtime_artifacts.py','fetch','--repository','{repository}'])
        self.assertEqual(prepare[-1],['python3','scripts/runtime_artifacts.py','verify'])
        self.assertEqual(self.config['integrationChecks'][0][-1],'{url:web}')

    def test_hooks_are_argument_arrays_and_point_to_existing_scripts(self):
        for section in ['prepare','checks','integrationChecks']:
            for command in self.config[section]:
                self.assertIsInstance(command,list)
                self.assertTrue(all(isinstance(arg,str) for arg in command))
                if len(command)>1 and command[1].endswith('.py'):
                    self.assertTrue((ROOT/command[1]).is_file())


if __name__=='__main__':unittest.main()
