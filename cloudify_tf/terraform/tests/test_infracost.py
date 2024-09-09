
import json
import unittest

from unittest.mock import MagicMock, patch, PropertyMock
from cloudify_tf.terraform.infracost import (
    Infracost,
    InfracostException,
    get_infracost_config
)


class TestInfracost(unittest.TestCase):

    def setUp(self):
        self.logger = MagicMock()
        self.deployment_name = 'test_deployment'
        self.node_instance_name = 'test_node_instance'
        self.infracost = Infracost(
            self.logger,
            self.deployment_name,
            self.node_instance_name,
            installation_source='source_url',
            executable_path='/path/to/executable',
            api_key='test_api_key',
            variables={'key': 'value'},
            environment_variables={
                'key1': True,
                'key2': False,
                'key3': 'string',
                'key4': 123
            },
            tfvars={'var': 'value'},
            enable=True
        )

        self.infracost.config = {'some': 'config'}

    @patch('cloudify_tf.terraform.infracost.Infracost.use_system_infracost')
    def test_executable_path(self,
                             mock_use_system_infracost):
        mock_use_system_infracost.return_value = True
        self.assertTrue(self.infracost.executable_path, '/path/to/executable')

    @patch('os.path.isfile')
    @patch('cloudify_tf.terraform.infracost.Infracost.node_instance_directory')
    def test_require_download_infracost(self,
                                        mock_node_instance_directory,
                                        mock_isfile):
        mock_isfile.return_value = True
        mock_node_instance_directory.return_value = 'node_instance_dir '
        result = Infracost.require_download_infracost(self.infracost,
                                                      '/path/to/executable')
        self.assertFalse(result)
        mock_isfile.return_value = False
        result = Infracost.require_download_infracost(self.infracost, None)
        self.assertTrue(result)

    @patch('os.path.isfile')
    @patch('cloudify_tf.terraform.infracost.Infracost.node_instance_directory',
           new_callable=PropertyMock)
    def test_use_system_infracost(self,
                                  mock_node_instance_directory,
                                  mock_isfile):
        mock_isfile.return_value = False
        mock_node_instance_directory.return_value = 'node_instance_dir'
        result = Infracost.use_system_infracost(self.infracost,
                                                '/path/to/executable')
        self.assertTrue(result)

        result = Infracost.use_system_infracost(self.infracost, None)
        self.assertFalse(result)

    def test_env(self):
        expected_output = {
            'key1': 'true',
            'key2': 'false',
            'key3': 'string',
            'key4': 123
        }

        for key, value in expected_output.items():
            self.assertEqual(self.infracost.env.get(key), value)

    def test_config_property_name(self):
        self.assertEqual(self.infracost.config_property_name,
                         'infracost_config')

    def test_installation_source(self):
        self.assertEqual(self.infracost.installation_source, 'source_url')

    def test_terraform_root_module(self):
        self.assertEqual(self.infracost.terraform_root_module, None)

    def test_tfvars(self):
        self.assertEqual(self.infracost.tfvars, {'var': 'value'})

    @patch('cloudify_tf.terraform.infracost.get_infracost_config')
    def test_from_ctx(self, mock_get_infracost_config):
        mock_get_infracost_config.return_value = {
            'variables': None,
            'environment_variables': None,
            'tfvars': None
        }
        mock_ctx = MagicMock()
        mock_ctx.node.properties = {'prop': 'value'}
        mock_ctx.instance.runtime_properties = {'runtime': 'value'}
        mock_ctx.logger = MagicMock()
        mock_ctx.deployment.id = 'test_deployment_id'
        mock_ctx.instance.id = 'test_instance_id'

        variables = {'key': 'value'}
        env = {'key1': True, 'key2': False}
        tfvars = {'var': 'value'}

        infracost_instance = Infracost.from_ctx(
            mock_ctx,
            variables=variables,
            env=env,
            tfvars=tfvars
        )

        self.assertEqual(infracost_instance.logger, mock_ctx.logger)
        self.assertEqual(infracost_instance.env, env)
        self.assertEqual(infracost_instance.tfvars, tfvars)
        self.assertTrue(isinstance(infracost_instance, Infracost))
        self.assertEqual(infracost_instance.variables, variables)

    @patch('cloudify_tf.terraform.infracost.Infracost.execute')
    @patch('cloudify_tf.terraform.infracost.Infracost.config_file')
    @patch('cloudify_tf.terraform.infracost.Infracost.runtime_file')
    @patch('cloudify_tf.terraform.infracost.convert_secrets')
    @patch('cloudify_tf.terraform.infracost.Infracost.node_instance_directory')
    @patch('cloudify_tf.terraform.infracost.Infracost.executable_path')
    @patch('os.fspath')
    def test_infracost(self,
                       mock_fspath,
                       mock_executable_path,
                       mock_node_instance_directory,
                       mock_convert_secrets,
                       mock_runtime_file,
                       mock_config_file,
                       mock_execute):
        mock_fspath.return_value = 'mock_fspath'
        mock_executable_path.return_value = 'mock_path'
        mock_node_instance_directory.return_value = 'dir'
        mock_runtime_file.return_value.__enter__.return_value = 'runtime_file'
        mock_config_file.return_value.__enter__.return_value = 'file_path'
        mock_convert_secrets.return_value = {'key1': 'true', 'key2': 'false'}

        mock_execute.side_effect = [
            'result_output',
            json.dumps({'json': 'result'})
        ]
        result, json_result = self.infracost.infracost()

        self.assertEqual(result, 'result_output')
        self.assertEqual(json_result, {'json': 'result'})
        self.assertEqual(mock_execute.call_count, 2)
        mock_convert_secrets.assert_called_with(self.infracost.env)

    @patch('cloudify_tf.terraform.infracost.Infracost.node_instance_directory',
           new_callable=PropertyMock)
    @patch('cloudify_tf.terraform.infracost.Infracost.executable_path',
           new_callable=PropertyMock)
    def test_export_config(self,
                           nock_executable_path,
                           mock_node_instance_directory):
        nock_executable_path.return_value = 'mock_path'
        mock_node_instance_directory.return_value = 'dir'

        expected_config = {
            'environment_variables': {
                'INFRACOST_API_KEY': 'test_api_key',
                'key1': 'true',
                'key2': 'false',
                'key3': 'string',
                'key4': 123
                },
            'executable_path': 'mock_path',
            'installation_source': 'source_url',
            'tfvars': {'var': 'value'},
            'variables': {'key': 'value'}
        }

        actual_config = self.infracost.export_config()
        self.assertEqual(expected_config, actual_config)

    @patch('cloudify_tf.terraform.infracost.Infracost._execute')
    @patch('time.sleep', return_value=None)
    def test_execute_success(self, mock_sleep, mock_execute):
        mock_execute.return_value = 'success_output'
        result = self.infracost.execute(['some_command'], '/cwd', {})
        self.assertEqual(result, 'success_output')

    @patch('cloudify_tf.terraform.infracost.Infracost._execute')
    def test_execute_non_recoverable_error(self, mock_execute):
        mock_execute.side_effect = Exception('Some other error')

        with self.assertRaises(InfracostException):
            self.infracost.execute(['some_command'], '/cwd', {})

    def test_node_props_has_config(self):
        node_props = {'infracost_config': {'key': 'value'}}
        instance_props = {}
        result = get_infracost_config(node_props, instance_props)
        expected = {'key': 'value'}
        self.assertEqual(result, expected)

    def test_neither_has_config(self):
        node_props = {}
        instance_props = {}
        result = get_infracost_config(node_props, instance_props)
        expected = {}
        self.assertEqual(result, expected)

    @patch('cloudify_tf.terraform.infracost.NamedTemporaryFile')
    @patch('cloudify_tf.terraform.infracost.remove')
    @patch('cloudify_tf.terraform.infracost.convert_secrets')
    def test_runtime_file_with_tfvars(self,
                                      mock_convert_secrets,
                                      mock_remove,
                                      mock_named_temp_file):
        self.infracost._tfvars = {'tfvar_key': 'tfvar_value'}
        with self.infracost.runtime_file() as result:
            self.assertEqual(result, self.infracost._tfvars)

    @patch('cloudify_tf.terraform.infracost.NamedTemporaryFile')
    @patch('cloudify_tf.terraform.infracost.remove')
    @patch('cloudify_tf.terraform.infracost.convert_secrets')
    def test_runtime_file_without_tfvars(self,
                                         mock_convert_secrets,
                                         mock_remove,
                                         mock_named_temp_file):
        self.infracost._tfvars = None
        mock_convert_secrets.return_value = {'key': 'value'}

        mock_file = MagicMock()
        mock_file.name = 'temp_file_path'
        mock_named_temp_file.return_value.__enter__.return_value = mock_file

        with self.infracost.runtime_file() as result:
            self.assertEqual(result, 'temp_file_path')
            mock_convert_secrets.assert_called_once_with(
                self.infracost.variables)
            mock_named_temp_file.assert_called_once_with(
                suffix=".json",
                delete=False,
                mode="w",
                dir=self.infracost.terraform_root_module
            )

        mock_remove.assert_called_once_with('temp_file_path')

    @patch('cloudify_tf.terraform.infracost.NamedTemporaryFile')
    @patch('cloudify_tf.terraform.infracost.remove')
    def test_config_file(self, mock_remove, mock_named_temp_file):
        mock_file = MagicMock()
        mock_file.name = 'temp_file_path'
        mock_named_temp_file.return_value.__enter__.return_value = mock_file

        with patch('yaml.dump') as mock_yaml_dump:
            with self.infracost.config_file() as file_path:
                self.assertEqual(file_path, 'temp_file_path')
                mock_yaml_dump.assert_called_once_with(
                    {'some': 'config'}, mock_file)

        mock_remove.assert_called_once_with('temp_file_path')
