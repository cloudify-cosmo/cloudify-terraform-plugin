
import os
import json
import shutil
import tempfile
import unittest

from script_runner.tasks import ProcessException
from cloudify_tf.terraform import Terraform, setup_config_tf
from unittest.mock import MagicMock, patch, PropertyMock
from distutils.version import LooseVersion as parse_version

from cloudify import exceptions as ne_exc

pkg = 'cloudify_'
CREATE_OP = 'interfaces.lifecycle.create'


class TestTerraformInitialization(unittest.TestCase):

    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.execute')
    def setUp(self, mock_execute, mock_set_plugins_dir):
        # Create a mock logger
        self.mock_logger = MagicMock()

        # Mock the set_plugins_dir method to avoid file system operations
        mock_set_plugins_dir.return_value = '/mock/path/to/plugins'
        self.mock_execute = mock_execute

        # Initialize the Terraform object with test parameters
        self.terraform = Terraform(
            logger=self.mock_logger,
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            variables={'key': 'value'},
            environment_variables={'env_key': 'env_value'},
            backend={'type': 'local'},
            provider={'aws': '2.0'},
            required_providers={'aws': {
                'source': 'hashicorp/aws', 'version': '2.0.0'}},
            provider_upgrade=True,
            additional_args=['-var', 'foo=bar'],
            version='1.0.0',
            flags_override=['-auto-approve'],
            log_stdout=False,
            tfvars='file.tfvars'
        )

    def test_initialization(self):
        self.assertEqual(self.terraform.tool_name, 'Terraform')
        self.assertEqual(self.terraform.binary_path,
                         '/usr/local/bin/terraform')
        self.assertEqual(self.terraform.plugins_dir, '/mock/path/to/plugins')
        self.assertEqual(self.terraform._root_module, '/path/to/root/module')
        self.assertEqual(self.terraform.logger, self.mock_logger)
        self.assertEqual(self.terraform.additional_args, ['-var', 'foo=bar'])
        self.assertEqual(self.terraform._version, '1.0.0')
        self.assertEqual(self.terraform._flags_override, ['-auto-approve'])
        self.assertFalse(self.terraform._log_stdout)
        self.assertEqual(self.terraform._variables, {'key': 'value'})
        self.assertEqual(self.terraform._env, {'env_key': 'env_value'})
        self.assertEqual(self.terraform._backend, {'type': 'local'})
        self.assertEqual(self.terraform._required_providers,
                         {'aws': {
                             'source': 'hashicorp/aws', 'version': '2.0.0'}})
        self.assertEqual(self.terraform._provider, {'aws': '2.0'})
        self.assertTrue(self.terraform.provider_upgrade)
        self.assertEqual(self.terraform._tfvars, 'file.tfvars')

    def test_root_module_property(self):
        with patch(
                f'{pkg}tf.terraform.utils.try_to_copy_old_state_file'
                ) as copy:
            self.terraform.root_module = '/new/path/to/root/module'
            self.assertEqual(self.terraform._root_module,
                             '/new/path/to/root/module')
            copy.assert_called_once_with('/new/path/to/root/module')

    def test_flags_property(self):
        with patch(f'{pkg}tf.terraform.Terraform._format_flags') as mock_flags:
            mock_flags.return_value = ['-formatted-flag']
            self.terraform._flags_override = ['-auto-approve']
            flags = self.terraform.flags
            mock_flags.assert_called_once_with(['-auto-approve'])
            self.assertEqual(flags, ['-formatted-flag'])

    def test_insecure_env_property(self):
        with patch(
                f'{pkg}tf.terraform.utils.convert_secrets'
                ) as convert_secret:
            convert_secret.return_value = {'env_key': 'secret_value'}
            self.terraform._env = {'env_key': 'value'}
            insecure_env = self.terraform.insecure_env
            convert_secret.assert_called_once_with({'env_key': 'value'})
            self.assertEqual(insecure_env, {'env_key': 'secret_value'})

    def test_env_property_setter(self):
        self.terraform._env = {'existing_key': 'existing_value'}
        self.terraform.env = {'new_key': 'new_value'}
        self.assertEqual(self.terraform._env,
                         {'existing_key': 'existing_value',
                          'new_key': 'new_value'})

    def test_insecure_variables_property(self):
        with patch(
                f'{pkg}tf.terraform.utils.convert_secrets'
                ) as convert_secrets:
            convert_secrets.return_value = {'var_key': 'secret_value'}
            self.terraform._variables = {'var_key': 'value'}
            insecure_variables = self.terraform.insecure_variables
            convert_secrets.assert_called_once_with({'var_key': 'value'})
            self.assertEqual(insecure_variables, {'var_key': 'secret_value'})

    def test_variables_property_setter(self):
        self.terraform._variables = {'existing_key': 'existing_value'}
        self.terraform.variables = {'new_key': 'new_value'}
        self.assertEqual(self.terraform._variables,
                         {'existing_key': 'existing_value',
                          'new_key': 'new_value'})

    def test_variables_property_setter_else(self):
        self.terraform._variables = None
        self.terraform.variables = {'new_key': 'new_value'}
        self.assertEqual(self.terraform._variables, {'new_key': 'new_value'})
        self.terraform.variables = {'another_key': 'another_value'}
        self.assertEqual(self.terraform.variables, {
            'new_key': 'new_value',
            'another_key': 'another_value'
        })

    def test_backend_property(self):
        with patch(f'{pkg}tf.terraform.utils.create_backend_string') as \
                mock_backend:
            self.terraform._backend = {
                'name': 'backend_name',
                'options': {'option1': 'value1'}
            }
            mock_backend.return_value = 'backend_string'
            backend = self.terraform.backend
            mock_backend.assert_called_once_with(
                'backend_name', {'option1': 'value1'}
            )
            self.assertEqual(backend, 'backend_string')

    def test_insecure_backend_property(self):
        with patch(
                f'{pkg}tf.terraform.utils.'
                'create_backend_string') as mock_backend, \
            patch(
                f'{pkg}tf.terraform.utils.'
                'convert_secrets') as convert_secrets:
            self.terraform._backend = {
                'name': 'backend_name',
                'options': {'option1': 'value1'}
            }
            convert_secrets.return_value = {
                'name': 'backend_name',
                'options': {'option1': 'secret_value'}
            }
            mock_backend.return_value = 'insecure_backend_string'
            insecure_backend = self.terraform.insecure_backend
            convert_secrets.assert_called_once_with({
                'name': 'backend_name',
                'options': {'option1': 'value1'}
            })
            mock_backend.assert_called_once_with(
                'backend_name', {'option1': 'secret_value'}
            )
            self.assertEqual(insecure_backend, 'insecure_backend_string')

    def test_required_providers_property(self):
        with patch(
                f'{pkg}tf.terraform.utils.'
                'create_required_providers_string') as m:
            self.terraform._required_providers = {
                'required_providers': {'provider1': 'version1'}
            }
            m.return_value = 'string'
            required_providers = self.terraform.required_providers
            m.assert_called_once_with(
                {'provider1': 'version1'}
            )
            self.assertEqual(required_providers, 'string')

    def test_provider_property(self):
        with patch(f'{pkg}tf.terraform.utils.create_provider_string') as \
                mock_create_provider_string:
            self.terraform._provider = {
                'providers': {'provider1': 'version1'}}
            mock_create_provider_string.return_value = 'provider_string'
            provider = self.terraform.provider
            mock_create_provider_string.assert_called_once_with(
                {'provider1': 'version1'})
            self.assertEqual(provider, 'provider_string')

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_get_valid_override_flags(self,
                                      mock_set_plugins_dir,
                                      mock_execute):
        help_result = """
        Usage: terraform [options]
        Options:
          -var=value    Set a variable
          -auto-approve  Automatically approve
        For more information on those options,
        run: terraform [subcommand] -help
        """
        nested_help_result = """
        Usage: terraform [subcommand] [options]
        Options:
          -force        Force apply
        """

        # Configure the mock to return the desired results
        mock_execute.return_value = help_result
        mock_execute.side_effect = [help_result, nested_help_result]
        mock_set_plugins_dir.return_value = '/mock/path/to/plugins'

        self.terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            additional_args=['-var', 'foo=bar'],
            environment_variables={},
            flags_override=['-auto-approve'],

        )
        command_args = ['subcommand', '-extra-flag']
        result_flags = self.terraform.get_valid_override_flags(command_args)

        self.assertEqual(result_flags, command_args)
        mock_execute.assert_any_call(
            ['/usr/local/bin/terraform', 'subcommand', '-help'])

    @patch(f'{pkg}tf.terraform.os.access')
    @patch(f'{pkg}tf.terraform.run_subprocess')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_execute_success(self,
                             mock_set_plugins_dir,
                             mock_run_subprocess,
                             mock_os_access):
        mock_os_access.return_value = True
        mock_run_subprocess.return_value = 'output'
        mock_set_plugins_dir.return_value = '/mock/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
            additional_args={
                'log_stdout': None
            }
        )

        command = ['/usr/local/bin/terraform', 'apply']
        result = terraform.execute(command)

        self.assertEqual(result, 'output')
        mock_run_subprocess.assert_called_once_with(
            command,
            terraform.logger,
            terraform.root_module,
            terraform.insecure_env,
            terraform.additional_args,
            return_output=True
        )

    @patch(f'{pkg}tf.terraform.os.access')
    @patch(f'{pkg}tf.terraform.run_subprocess')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_execute_process_exception(self,
                                       mock_set_plugins_dir,
                                       mock_run_subprocess,
                                       mock_os_access):
        mock_os_access.return_value = True
        mock_set_plugins_dir.return_value = '/mock/path/to/plugins'
        mock_run_subprocess.side_effect = ProcessException(
            exit_code=2,
            stderr='panic: runtime error: invalid memory address or nil '
            'pointer dereference',
            command='some_command'
        )

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
            additional_args={
                'log_stdout': None
            }
        )
        command = ['/usr/local/bin/terraform', 'apply']
        with self.assertRaises(ne_exc.OperationRetry):
            terraform.execute(command)

        mock_run_subprocess.side_effect = ProcessException(
            exit_code=1,
            stderr='error',
            command='some_command'
        )

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
            additional_args={
                'log_stdout': None
            }
        )
        command = ['/usr/local/bin/terraform', 'apply']
        with self.assertRaises(ProcessException):
            terraform.execute(command)

    @patch(f'{pkg}tf.terraform.Terraform.get_valid_override_flags')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_tf_command_with_flags(self,
                                   mock_set_plugins_dir,
                                   mock_get_valid_override_flags):
        # Set up the mock
        mock_get_valid_override_flags.return_value = [
            '--var', '--auto-approve']
        mock_set_plugins_dir.return_value = '/mock/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
            additional_args={'log_stdout': True},
            flags_override=['-auto-approve'],
        )

        args = ['apply', '-var', 'foo=bar']
        expected_command = ['/usr/local/bin/terraform',
                            'apply',
                            '-var',
                            'foo=bar']
        command = terraform._tf_command(args)
        self.assertEqual(command, expected_command)

    @patch(f'{pkg}tf.terraform.tempfile.NamedTemporaryFile')
    @patch(f'{pkg}tf.terraform.os.remove')
    @patch(f'{pkg}tf.terraform.delete_debug')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_runtime_file_with_tfvars(self,
                                      mock_set_plugins_dir,
                                      mock_delete_debug,
                                      mock_os_remove,
                                      mock_named_temp_file):
        mock_set_plugins_dir.return_value = '/mock/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
            additional_args={'log_stdout': True},
            tfvars='path/to/vars.tfvars'
        )

        command = ['apply']

        with terraform.runtime_file(command):
            self.assertIn('-var-file=path/to/vars.tfvars', command)

    @patch(f'{pkg}tf.terraform.tempfile.NamedTemporaryFile')
    @patch(f'{pkg}tf.terraform.os.remove')
    @patch(f'{pkg}tf.terraform.delete_debug')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_runtime_file_without_tfvars(self,
                                         mock_set_plugins_dir,
                                         mock_delete_debug,
                                         mock_os_remove,
                                         mock_named_temp_file):
        # Mock NamedTemporaryFile
        mock_temp_file = MagicMock()
        mock_temp_file.name = '/path/to/tempfile.json'
        mock_named_temp_file.return_value = mock_temp_file
        mock_set_plugins_dir.return_value = '/mock/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
            additional_args={'log_stdout': True},
        )

        command = ['apply']

        with terraform.runtime_file(command):
            self.assertIn('-var-file', command)
            self.assertIn('apply', command)

        # Check if the temp file was created and removed
        mock_named_temp_file.assert_called_once_with(
            suffix=".json", delete=False, mode="w", dir='/path/to/root/module')
        mock_delete_debug.return_value = True

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform._tf_command')
    @patch(f'{pkg}tf.terraform.Terraform.read_version')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_version_property_cached(self,
                                     mock_set_plugins_dir,
                                     mock_read_version,
                                     mock_tf_command,
                                     mock_execute):
        mock_read_version.return_value = '1.0.0'
        mock_execute.return_value = '{"version": "1.0.0"}'
        mock_set_plugins_dir.return_value = '/mock/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/mock/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
            additional_args={'log_stdout': True}
        )

        version = terraform.version

        self.assertEqual(version, '1.0.0')
        mock_execute.assert_called_once()
        mock_tf_command.assert_called_once_with(['version', '-json'])
        mock_read_version.assert_called_once_with('{"version": "1.0.0"}')

    def test_read_version_from_text_success(self):
        text = b'Terraform v1.2.3\n'
        version = Terraform.read_version_from_text(text)
        self.assertEqual(version, '1.2.3')

    def test_read_version_from_text_invalid_format(self):
        text = b'Terraform vXYZ\n'
        version = Terraform.read_version_from_text(text)
        self.assertEqual(version, '0.0.0')

    @patch(f'{pkg}tf.terraform.Terraform.read_version_from_text')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_read_version_json(self,
                               mock_set_plugins_dir,
                               mock_read_version_from_text):
        mock_read_version_from_text.return_value = '1.2.3'
        mock_set_plugins_dir.return_value = '/mock/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/mock/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
            additional_args=['-var', 'foo=bar'],
        )

        response = json.dumps({'version': '1.2.3'})
        version_info = terraform.read_version(response)

        self.assertEqual(version_info, {'version': '1.2.3'})
        mock_read_version_from_text.assert_not_called()

    @patch(f'{pkg}tf.terraform.Terraform.read_version_from_text')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_read_version_non_json(self,
                                   mock_set_plugins_dir,
                                   mock_read_version_from_text):
        mock_set_plugins_dir.return_value = '/mock/path/to/plugins'
        response = b'Terraform v1.2.3\n'
        mock_read_version_from_text.return_value = '1.2.3'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/mock/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
            additional_args=['-var', 'foo=bar'],
        )

        version_info = terraform.read_version(response)

        self.assertEqual(version_info, {
            'terraform_version': '1.2.3',
            'terraform_outdated': True
        })
        mock_read_version_from_text.assert_called_once_with(response)

    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.version', new_callable=PropertyMock)
    def test_terraform_version_and_terraform_outdated_property(
            self,
            mock_version,
            mock_set_plugins_dir):

        mock_version.return_value = {
            'terraform_version': '1.2.3',
            'terraform_outdated': False
        }
        mock_set_plugins_dir.return_value = '/mock/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/mock/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )
        self.assertEqual(terraform.terraform_version, '1.2.3')
        self.assertEqual(terraform.terraform_outdated, False)

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.runtime_file')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_init_with_all_flags(self,
                                 mock_set_plugins_dir,
                                 mock_runtime_file,
                                 mock_execute):
        mock_runtime_file.return_value.__enter__.return_value = None
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            provider_upgrade=True,
            root_module='/path/to/root/module',
            environment_variables={},
        )

        command_line_args = ['-backend-config=foo=bar']
        prefix = ['custom-prefix']

        expected_command = [
            'custom-prefix', '/usr/local/bin/terraform', 'init', '-no-color',
            '-input=false',
            '--plugin-dir=/path/to/plugins',
            '--upgrade',
            '-backend-config=foo=bar'
        ]

        mock_execute.return_value = 'output'
        result = terraform.init(
            command_line_args=command_line_args, prefix=prefix)
        mock_execute.assert_called_once_with(expected_command)
        self.assertEqual(result, 'output')

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.runtime_file')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_destroy(self,
                     mock_set_plugins_dir,
                     mock_runtime_file,
                     mock_execute):
        mock_execute.return_value = 'Destroy output'
        mock_runtime_file.return_value = MagicMock()
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        result = terraform.destroy()

        self.assertEqual(result, 'Destroy output')
        expected_command = [
            '/usr/local/bin/terraform', 'destroy',
            '-auto-approve', '-no-color', '-input=false'
        ]
        mock_execute.assert_called_once_with(expected_command)
        mock_runtime_file.assert_called_once_with(expected_command)

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.runtime_file')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_plan(self, mock_set_plugins_dir, mock_runtime_file, mock_execute):
        mock_execute.return_value = 'Plan output'
        mock_runtime_file.return_value = MagicMock()
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        result = terraform.plan()
        self.assertEqual(result, 'Plan output')

        expected_command = [
            '/usr/local/bin/terraform', 'plan',
            '-no-color', '-input=false'
        ]
        mock_execute.assert_called_once_with(expected_command, False)
        mock_runtime_file.assert_called_once_with(expected_command)

        mock_execute.reset_mock()
        out_file_path = '/path/to/plan.tfplan'
        result = terraform.plan(out_file_path)
        self.assertEqual(result, 'Plan output')

        expected_command.extend(['-out', out_file_path])
        mock_execute.assert_called_once_with(expected_command, False)

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_output_success(self, mock_set_plugins_dir, mock_execute):
        mock_execute.return_value = '{"key": "value"}'
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        result = terraform.output()
        self.assertEqual(result, {"key": "value"})

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_output_malformed_json(self, mock_set_plugins_dir, mock_execute):
        mock_execute.return_value = \
            '{"key": "value"}\n{"another_key": "another_value"}'
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        result = terraform.output()
        self.assertEqual(
            result, [{"key": "value"}, {"another_key": "another_value"}])

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_output_json_decode_error(self,
                                      mock_set_plugins_dir,
                                      mock_execute):
        mock_execute.return_value = 'invalid json'
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        with self.assertRaises(json.JSONDecodeError):
            terraform.output()

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.runtime_file')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_apply(self,
                   mock_set_plugins_dir,
                   mock_runtime_file,
                   mock_execute):
        mock_execute.return_value = 'Apply successful'
        mock_runtime_file.return_value.__enter__.return_value = None
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        result = terraform.apply()
        self.assertEqual(result, 'Apply successful')
        mock_execute.assert_called_once_with(
            terraform._tf_command(
                ['apply', '-auto-approve', '-no-color', '-input=false'])
        )

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_graph(self, mock_set_plugins_dir, mock_execute):
        mock_execute.return_value = 'Graph output'
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        result = terraform.graph()
        self.assertEqual(result, 'Graph output')
        mock_execute.assert_called_once_with(
            terraform._tf_command(['graph'])
        )

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_state_pull(self, mock_set_plugins_dir, mock_execute):
        mock_execute.return_value = '{"key": "value"}'
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        result = terraform.state_pull()
        self.assertEqual(result, {'key': 'value'})
        mock_execute.assert_called_once_with(
            terraform._tf_command(['state', 'pull']),
            False
        )

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_state_pull_invalid_json(self,
                                     mock_set_plugins_dir,
                                     mock_execute):
        mock_execute.return_value = '{"key": "value"}\n{"key2": "value2"}'
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        result = terraform.state_pull()
        self.assertEqual(result, [{'key': 'value'}, {'key2': 'value2'}])
        mock_execute.assert_called_once_with(
            terraform._tf_command(['state', 'pull']),
            False
        )

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.parse_version')
    @patch(f'{pkg}tf.terraform.Terraform.terraform_version',
           new_callable=MagicMock)
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_refresh_new_version(self,
                                 mock_set_plugins_dir,
                                 mock_terraform_version,
                                 mock_parse_version,
                                 mock_execute):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir)

        mock_parse_version.return_value = parse_version("0.15.4")
        mock_terraform_version.return_value = '0.15.4'
        mock_execute.return_value = 'output'
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module=self.temp_dir,  # Use temporary directory
            environment_variables={},
        )

        result = terraform.refresh()
        self.assertEqual(result, 'output')
        mock_execute.assert_called()

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_state_list_with_state_file(self,
                                        mock_set_plugins_dir,
                                        mock_execute):
        mock_execute.return_value = 'resource_list'
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        plan_file_path = '/path/to/statefile.tfstate'
        result = terraform.state_list(plan_file_path=plan_file_path)
        expected_command = terraform._tf_command(
            ['state', 'list', f'-state={plan_file_path}'])

        self.assertEqual(result, 'resource_list')
        mock_execute.assert_called_once_with(expected_command)

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_show_with_plan_file(self, mock_set_plugins_dir, mock_execute):
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        plan_file_path = '/path/to/planfile.tfplan'
        output_data = '{"resource": "example"}'  # Example JSON output
        mock_execute.return_value = output_data

        result = terraform.show(plan_file_path=plan_file_path)
        expected_command = terraform._tf_command(
            ['show', '-no-color', '-json', plan_file_path])

        self.assertEqual(result, {"resource": "example"})
        mock_execute.assert_called_once_with(expected_command, False)

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_show_without_plan_file(self, mock_set_plugins_dir, mock_execute):
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        output_data = '{"resource": "example"}'
        mock_execute.return_value = output_data

        result = terraform.show()
        expected_command = terraform._tf_command(
            ['show', '-no-color', '-json'])

        self.assertEqual(result, {"resource": "example"})
        mock_execute.assert_called_once_with(expected_command, False)

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_show_plain_text_with_plan_file(self,
                                            mock_set_plugins_dir,
                                            mock_execute):
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        plan_file_path = '/path/to/planfile.tfplan'
        output_data = 'resource "example" { ... }'
        mock_execute.return_value = output_data

        result = terraform.show_plain_text(plan_file_path=plan_file_path)
        expected_command = terraform._tf_command(
            ['show', '-no-color', plan_file_path])

        self.assertEqual(result, output_data)
        mock_execute.assert_called_once_with(expected_command)

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_show_plain_text_without_plan_file(self,
                                               mock_set_plugins_dir,
                                               mock_execute):
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        output_data = 'resource "example" { ... }'
        mock_execute.return_value = output_data

        result = terraform.show_plain_text()
        expected_command = terraform._tf_command(['show', '-no-color'])

        self.assertEqual(result, output_data)
        mock_execute.assert_called_once_with(expected_command)

    @patch(f'{pkg}tf.terraform.Terraform.show')
    @patch(f'{pkg}tf.terraform.Terraform.plan')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_plan_and_show(self, mock_set_plugins_dir, mock_plan, mock_show):
        mock_plan.return_value = None
        mock_show.return_value = {"key": "value"}
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        result = terraform.plan_and_show()

        mock_plan.assert_called_once()
        mock_show.assert_called_once()
        self.assertEqual(result, {"key": "value"})

    @patch(f'{pkg}tf.terraform.Terraform.show_plain_text')
    @patch(f'{pkg}tf.terraform.Terraform.show')
    @patch(f'{pkg}tf.terraform.Terraform.plan')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_plan_and_show_two_formats(self,
                                       mock_set_plugins_dir,
                                       mock_plan,
                                       mock_show,
                                       mock_show_plain_text):
        mock_plan.return_value = None
        mock_show.return_value = '{"key": "value"}'
        mock_show_plain_text.return_value = 'key = value\n'
        mock_set_plugins_dir.return_value = None

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        json_result, plain_text_result = terraform.plan_and_show_two_formats()

        mock_plan.assert_called_once()
        mock_show.assert_called_once()
        mock_show_plain_text.assert_called_once()
        self.assertEqual(json_result, '{"key": "value"}')
        self.assertEqual(plain_text_result, 'key = value\n')

    @patch(f'{pkg}tf.terraform.Terraform._show_state_resource_list')
    @patch(f'{pkg}tf.terraform.Terraform._show_state_of_modules')
    @patch(f'{pkg}tf.terraform.Terraform.refresh')
    @patch(f'{pkg}tf.terraform.Terraform.show')
    @patch(f'{pkg}tf.terraform.Terraform.plan')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_plan_and_show_state(self,
                                 mock_set_plugins_dir,
                                 mock_plan,
                                 mock_show,
                                 mock_refresh,
                                 mock_show_state_of_modules,
                                 mock_show_state_resource_list):
        mock_plan.return_value = None
        example_show_output = {
            'planned_values': {
                'root_module': {
                    'resources': {'example_resource': 'value'},
                    'child_modules': {'example_module': 'module_data'}
                }
            }
        }
        mock_show.return_value = example_show_output
        mock_refresh.return_value = None
        mock_show_state_resource_list.return_value = ['resource_problem']
        mock_show_state_of_modules.return_value = ['module_problem']
        mock_set_plugins_dir.return_value = None

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        status_problems = terraform.plan_and_show_state()

        mock_plan.assert_called_once()
        mock_show.assert_called_once()
        mock_refresh.assert_called_once()

        mock_show_state_resource_list.assert_called_once_with(
            {'example_resource': 'value'})
        mock_show_state_of_modules.assert_called_once_with(
            {'example_module': 'module_data'})
        expected_problems = ['resource_problem', 'module_problem']
        self.assertEqual(status_problems, expected_problems)

    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform._show_state_resource_list')
    def test_show_state_of_modules(self,
                                   mock_show_state_resource_list,
                                   mock_set_plugins_dir):
        mock_set_plugins_dir.return_value = None
        mock_show_state_resource_list.return_value = ['resource_problem']

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        example_modules = [
            {'resources': {'resource1': 'value1'}},
            {'resources': {'resource2': 'value2'}},
            {'non_resources_key': 'value'}
        ]

        status_problems = terraform._show_state_of_modules(example_modules)

        mock_show_state_resource_list.assert_any_call({'resource1': 'value1'})
        mock_show_state_resource_list.assert_any_call({'resource2': 'value2'})

        expected_problems = ['resource_problem', 'resource_problem']
        self.assertEqual(status_problems, expected_problems)

    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.show_state')
    def test_show_state_resource_list(self,
                                      mock_show_state,
                                      mock_set_plugins_dir):
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        def side_effect(address, state_file):
            if address == 'problematic_resource':
                raise Exception("Test Exception")

        mock_show_state.side_effect = side_effect

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        resources = [
            {'address': 'valid_resource'},
            {'address': 'problematic_resource'}
        ]

        status_problems = terraform._show_state_resource_list(resources)

        self.assertIn({'address': 'problematic_resource'}, status_problems)
        self.assertNotIn({'address': 'valid_resource'}, status_problems)

        mock_show_state.assert_any_call(
            'valid_resource', '/path/to/root/module/terraform.tfstate')
        mock_show_state.assert_any_call(
            'problematic_resource', '/path/to/root/module/terraform.tfstate')
        mock_set_plugins_dir.assert_called_once_with('/path/to/plugins')

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_show_state(self, mock_set_plugins_dir, mock_execute):
        mock_set_plugins_dir.return_value = '/path/to/plugins'
        mock_execute.return_value = 'resource state output'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        resource_name = 'example_resource'
        plan_file_path = '/path/to/plan.tfstate'
        expected_command = terraform._tf_command(
            ['state',
             'show',
             '-no-color',
             '-state={}'.format(plan_file_path),
             resource_name]
        )

        result = terraform.show_state(resource_name, plan_file_path)

        self.assertEqual(result, 'resource state output')
        mock_execute.assert_called_once_with(expected_command)
        mock_set_plugins_dir.assert_called_once_with('/path/to/plugins')

    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.runtime_file')
    def test_import_resource(self,
                             mock_runtime_file,
                             mock_set_plugins_dir,
                             mock_execute):
        mock_set_plugins_dir.return_value = '/path/to/plugins'
        mock_execute.return_value = 'import output'
        mock_runtime_file.return_value.__enter__.return_value = None

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        resource_address = 'aws_instance.example'
        resource_id = 'i-1234567890abcdef0'
        expected_command = terraform._tf_command(
            ['import', '-no-color', resource_address, resource_id]
        )

        result = terraform.import_resource(resource_address, resource_id)

        self.assertEqual(result, 'import output')
        mock_execute.assert_called_once_with(expected_command)
        mock_set_plugins_dir.assert_called_once_with('/path/to/plugins')
        mock_runtime_file.assert_called()

    @patch(f'{pkg}tf.terraform.Terraform.runtime_file')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.terratag', autospec=True)
    def test_run_terratag(self,
                          mock_terratag,
                          mock_set_plugins_dir,
                          mock_runtime_file):
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        terraform.terratag = MagicMock()
        terraform.terratag.validate = MagicMock()
        terraform.terratag.terratag = MagicMock()
        terraform.root_module = 'some_module'
        terraform.binary_path = '/path/to/binary'

        original_path = os.environ.get('PATH', '')
        os.environ['PATH'] = \
            f'{original_path}:{os.path.dirname(terraform.binary_path)}'

        terraform.run_terratag()

        terraform.terratag.validate.assert_called_once()
        self.assertEqual(
            terraform.terratag.terraform_root_module, terraform.root_module)
        terraform.terratag.terratag.assert_called_once()
        os.environ['PATH'] = original_path

    @patch(f'{pkg}tf.terraform.Terraform.runtime_file')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.terratag', autospec=True)
    def test_run_terratag_not_terratag(self,
                                       mock_terratag,
                                       mock_set_plugins_dir,
                                       mock_runtime_file):
        mock_set_plugins_dir.return_value = '/path/to/plugins'

        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        terraform.terratag = None
        terraform.run_terratag()
        self.assertEqual(terraform.terratag, None)

    @patch(f'{pkg}tf.terraform.Terraform.runtime_file')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    def test_run_terratag_binary_path_in_path(self,
                                              mock_set_plugins_dir,
                                              mock_runtime_file):
        mock_set_plugins_dir.return_value = '/path/to/plugins'
        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        terraform.terratag = MagicMock()
        terraform.terratag.validate = MagicMock()
        terraform.terratag.terratag = MagicMock()
        terraform.root_module = 'some_module'

        original_path = os.environ.get('PATH', '')
        new_path_dir = os.path.dirname(terraform.binary_path)
        os.environ['PATH'] = original_path.replace(f"{new_path_dir}:", "")

        terraform.run_terratag()
        self.assertIn(new_path_dir, os.environ['PATH'])

    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.plan_file')
    def test_check_opa_no_opa(self, mock_plan_file, mock_set_plugins_dir):
        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        terraform.opa = None
        result = terraform.check_opa()
        self.assertIsNone(result)

    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.plan_file')
    @patch(f'{pkg}tf.terraform.opa')
    def test_check_opa_with_opa(self,
                                mock_opa,
                                mock_plan_file,
                                mock_set_plugins_dir):
        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        mock_opa = MagicMock()
        terraform.opa = mock_opa

        mock_plan_file.return_value.__enter__ = MagicMock(
            return_value='path/to/plan/file')
        mock_plan_file.return_value.__exit__ = MagicMock(return_value=False)

        decision = 'allow'

        terraform.check_opa(decision=decision)

        mock_opa.validate.assert_called_once()
        self.assertEqual(
            terraform.opa.terraform_root_module, terraform.root_module)
        mock_opa.evaluate_policy.assert_called_once_with(
            input_file='path/to/plan/file',
            decision=decision
        )

    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.infracost')
    def test_run_infracost_no_infracost(self,
                                        mock_infracost,
                                        mock_set_plugins_dir):
        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )
        terraform.infracost = None
        result = terraform.run_infracost()
        self.assertIsNone(result)

    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.infracost')
    def test_run_infracost_with_infracost(self,
                                          mock_infracost,
                                          mock_set_plugins_dir):
        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
        )

        mock_infracost_instance = MagicMock()
        terraform.infracost = mock_infracost_instance

        mock_infracost_instance.infracost.return_value = 'result'
        original_path = os.environ.get('PATH', '')
        new_path_dir = os.path.dirname(terraform.binary_path)
        os.environ['PATH'] = original_path.replace(f"{new_path_dir}:", "")

        result = terraform.run_infracost()

        mock_infracost_instance.validate.assert_called_once()
        self.assertEqual(
            terraform.infracost.terraform_root_module, terraform.root_module)
        self.assertIn(new_path_dir, os.environ['PATH'])
        self.assertEqual(result, 'result')

        os.environ['PATH'] = original_path

    @patch(f'{pkg}tf.terraform.update_dict_values')
    @patch(f'{pkg}tf.terraform.TFLint')
    @patch(f'{pkg}tf.terraform.TFSec')
    @patch(f'{pkg}tf.terraform.Terratag')
    @patch(f'{pkg}tf.terraform.Infracost')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.terraform_outdated', autospec=True)
    @patch(f'{pkg}tf.terraform.Terraform.terraform_version', autospec=True)
    def test_setup_config_tf(self,
                             mock_terraform_version,
                             mock_terraform_outdated,
                             mock_set_plugins_dir,
                             mock_infracost,
                             mock_terratag,
                             mock_tfsec,
                             mock_tflint,
                             mock_update_dict_values):
        mock_ctx = MagicMock()
        terraform = Terraform(
            logger=MagicMock(),
            binary_path='/usr/local/bin/terraform',
            plugins_dir='/path/to/plugins',
            root_module='/path/to/root/module',
            environment_variables={},
            additional_args=['-var', 'foo=bar'],
            log_stdout=False,
        )
        mock_terraform_outdated.return_value = True
        mock_terraform_version.return_value = '0.12.0'
        mock_ctx.operation.name = f"create_{CREATE_OP}"

        mock_ctx.logger.info = MagicMock()

        mock_update_dict_values.return_value = {'updated_key': 'updated_value'}

        mock_tflint_instance = MagicMock()
        mock_tfsec_instance = MagicMock()
        mock_terratag_instance = MagicMock()
        mock_infracost_instance = MagicMock()

        mock_tflint.from_ctx.return_value = mock_tflint_instance
        mock_tfsec.from_ctx.return_value = mock_tfsec_instance
        mock_terratag.from_ctx.return_value = mock_terratag_instance
        mock_infracost.from_ctx.return_value = mock_infracost_instance

        setup_config_tf(
            ctx=mock_ctx,
            tf=terraform,
            tflint_config={'enable': True},
            tfsec_config={'enable': True},
            terratag_config={'enable': True, 'tags': {'key': 'value'}},
            infracost_config={'enable': True}
        )

        mock_tflint.from_ctx.assert_called_once_with(
            _ctx=mock_ctx, tflint_config={'updated_key': 'updated_value',
                                          'tags': {}})
        mock_tflint_instance.export_config.assert_called_once()

        mock_tfsec.from_ctx.assert_called_once_with(
            _ctx=mock_ctx, tfsec_config={'updated_key': 'updated_value',
                                         'tags': {}})
        mock_tfsec_instance.export_config.assert_called_once()

        mock_terratag.from_ctx.assert_called_once_with(
            _ctx=mock_ctx,
            terratag_config={'updated_key': 'updated_value', 'tags': {}})
        mock_terratag_instance.export_config.assert_called_once()

        mock_infracost.from_ctx.assert_called_once_with(
            _ctx=mock_ctx,
            infracost_config={'updated_key': 'updated_value', 'tags': {}},
            variables=terraform.variables,
            env=terraform.env,
            tfvars=terraform.tfvars
        )
        mock_infracost_instance.export_config.assert_called_once()
