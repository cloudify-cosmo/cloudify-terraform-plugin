# Copyright © 2024 Dell Inc. or its subsidiaries. All Rights Reserved.


import unittest
from mock import MagicMock, patch, PropertyMock

from cloudify_tf.terraform import tflint

pkg = 'cloudify_'

TFLINT_URL = 'https://github.com/terraform-linters/tflint/releases/download/' \
             'latest/tflint_amd64.zip'

CONFIG_RESULT = """rule "terraform_unused_declarations" {
   terraform_unused_declarations = true

}
plugin "foo" {
   enabled = true
   version = "0.1.0"
   source = "github.com/org/tflint-ruleset-foo"
   signing_key = <<-KEY
   -----BEGIN PGP PUBLIC KEY BLOCK-----

   mQINBFzpPOMBEADOat4P4z0jvXaYdhfy+UcGivb2XYgGSPQycTgeW1YuGLYdfrwz
   9okJj9pMMWgt/HpW8WrJOLv7fGecFT3eIVGDOzyT8j2GIRJdXjv8ZbZIn1Q+1V72
   AkqlyThflWOZf8GFrOw+UAR1OASzR00EDxC9BqWtW5YZYfwFUQnmhxU+9Cd92e6i
   ...
   KEY

}
config {
   module = true
   ignore_module {
      terraform-aws-modules/vpc/aws = true
      terraform-aws-modules/security-group/aws = true

   }
   varfile = "[example1.tfvars, example2.tfvars]"
   variables = "[foo=bar, bar=[baz]]"

}
"""

KEY = """<<-KEY
-----BEGIN PGP PUBLIC KEY BLOCK-----

mQINBFzpPOMBEADOat4P4z0jvXaYdhfy+UcGivb2XYgGSPQycTgeW1YuGLYdfrwz
9okJj9pMMWgt/HpW8WrJOLv7fGecFT3eIVGDOzyT8j2GIRJdXjv8ZbZIn1Q+1V72
AkqlyThflWOZf8GFrOw+UAR1OASzR00EDxC9BqWtW5YZYfwFUQnmhxU+9Cd92e6i
...
KEY"""


def get_tf_tools_params():
    info_logger = MagicMock()
    error_logger = MagicMock()
    logger_mock = MagicMock()
    logger_mock.info = info_logger
    logger_mock.error = error_logger
    params = {
        'logger': logger_mock,
        'deployment_name': 'deployment_name_test',
        'node_instance_name': 'node_instance_name_test'
    }
    return logger_mock, params, info_logger, error_logger


def tflint_params():   # noqa
    args, kwargs, info, error = get_tf_tools_params()
    kwargs.update({
        'installation_source': TFLINT_URL,
        'executable_path': None,
        'config': [
            {
                'type_name': 'config',
                'option_value': {
                    'module': 'true',
                    'ignore_module': {
                        'terraform-aws-modules/vpc/aws': 'true',
                        'terraform-aws-modules/security-group/aws': 'true',
                    }
                }
            },
            {
                'type_name': 'config',
                'option_value': {
                    'varfile': [
                        "example1.tfvars",
                        "example2.tfvars"
                    ]
                }
            },
            {
                'type_name': 'config',
                'option_value': {
                    'variables': [
                        "foo=bar",
                        "bar=[baz]"
                    ]
                }
            },
            {
                'type_name': 'rule',
                'option_name': 'terraform_unused_declarations',
                'option_value': {
                    'terraform_unused_declarations': 'true'
                },
            },
            {
                'type_name': 'plugin',
                'option_name': 'foo',
                'option_value': {
                    'enabled': 'true',
                    'version': '0.1.0',
                    'source': 'github.com/org/tflint-ruleset-foo',
                    'signing_key': KEY
                },
            },
        ],
        'flags_override': [{'loglevel': 'trace'}, 'force'],
        'env': {
            'TFLINT_LOG': 'debug'
        },
        'enable': True
    })
    return args, kwargs, info, error


@patch(f'{pkg}common_sdk.cli_tool_base.sdk_utils')
def test_validate(sdk_utils_mock):
    args, kwargs, info, error = tflint_params()
    download_file_mock = MagicMock()
    get_deployment_dir_mock = MagicMock(return_value='foo')
    sdk_utils_mock.download_file = download_file_mock
    sdk_utils_mock.get_deployment_dir = get_deployment_dir_mock
    tflint_instance = tflint.TFLint(**kwargs)
    tflint_instance.tool_name = 'test_validate'
    tflint_instance.validate()
    download_file_mock.assert_called_once_with(
        'foo/node_instance_name_test', TFLINT_URL)
    assert tflint_instance.flags == ['--loglevel=trace', '--force']
    assert tflint_instance.config == CONFIG_RESULT


class TestTFLint(unittest.TestCase):

    def setUp(self):
        self.logger = MagicMock()
        self.deployment_name = 'test_deployment'
        self.node_instance_name = 'test_node_instance'
        self.executable_path = '/path/to/executable'
        self.config_file = '/path/to/config_file'
        self.variable_file = '/path/to/variable_file'
        self.tf_root_module = '/path/to/terraform_root_module'
        self.env = {'key': 'value'}

        self.tflint = tflint.TFLint(
            self.logger,
            self.deployment_name,
            self.node_instance_name,
            installation_source='source_url',
            executable_path='/path/to/executable',
            config={'some': 'config', 'type_name': 'type_name_test'},
            flags_override=['--flag'],
            env={'key': 'value'},
            enable=True
        )

    def test_default_values(self):
        tflint_default = tflint.TFLint(
            self.logger,
            self.deployment_name,
            self.node_instance_name
        )
        self.assertEqual(tflint_default._installation_source, None)
        self.assertEqual(tflint_default.flags, [])
        self.assertEqual(tflint_default.env, {})
        self.assertEqual(tflint_default.config_property_name, 'tflint_config')
        self.assertIsNone(tflint_default.terraform_root_module)

    @patch('os.path.isfile')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.use_system_tflint')
    def test_require_download_tflint_file_exists(self,
                                                 mock_isfile,
                                                 mock_use_system_tflint):
        mock_use_system_tflint.return_value = True
        mock_isfile.return_value = True
        result = self.tflint.require_download_tflint(
            '/path/to/executable')

        # Check results
        self.assertFalse(result)
        self.assertEqual(
            self.tflint.executable_path, '/path/to/executable')
        mock_isfile.assert_called_once_with('/path/to/executable')

    @patch(f'{pkg}tf.terraform.tflint.TFLint.convert_config_to_hcl')
    def test_format_config_with_errors(self, mock_convert_config_to_hcl):
        self.tflint._config_from_props = [
            {'type_name': 'unsupported_type_1'},
            {'type_name': 'unsupported_type_2'}
        ]
        mock_convert_config_to_hcl.return_value = 'converted_hcl'

        result = self.tflint._format_config()

        expected_errors = [
            'Config option unsupported_type_1 is not supported.',
            'Config option unsupported_type_2 is not supported.'
        ]
        self.assertEqual(self.tflint._validation_errors, expected_errors)
        self.assertEqual(result, 'converted_hcl')

    def test_instance_props_has_config(self):
        node_props = {'tflint_config': {'key': 'node_value'}}
        instance_props = {'tflint_config': {'key': 'instance_value'}}
        result = tflint.get_tflint_config(node_props, instance_props)
        self.assertEqual(result, {'key': 'instance_value'})

    def test_both_props_dont_have_config(self):
        node_props = {}
        instance_props = {}
        result = tflint.get_tflint_config(node_props, instance_props)
        self.assertEqual(result, {})

    def test_setter(self):
        self.tflint.env = {'new': 'value'}
        self.assertEqual(self.tflint.env, {'new': 'value'})
        self.tflint.installation_source = 'installation_source'
        self.assertEqual(self.tflint.installation_source,
                         'installation_source')

    @patch(f'{pkg}tf.terraform.tflint.path.exists')
    @patch(f'{pkg}tf.terraform.tflint.remove')
    @patch(f'{pkg}tf.terraform.tflint.NamedTemporaryFile')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.config',
           new_callable=PropertyMock)
    def test_configfile_success(self,
                                mock_config,
                                mock_named_temp_file,
                                mock_remove,
                                mock_exists):
        mock_exists.return_value = True

        mock_config.return_value = '{"key": "value"}'
        mock_file = MagicMock()
        mock_named_temp_file.return_value.__enter__.return_value = mock_file

        with self.tflint.configfile() as config_file_name:
            self.assertEqual(config_file_name, mock_file.name)
            mock_named_temp_file.assert_called_once_with(
                dir=None, delete=False
            )
            mock_file.write.assert_called_once_with(b'{"key": "value"}')
            mock_file.flush.assert_called_once()

        mock_remove.assert_called_once_with(mock_file.name)

    @patch(f'{pkg}tf.terraform.tflint.TFLint._init')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.configfile')
    def test_init(self, mock_configfile, mock_init):
        mock_file_name = '/mock/path/to/config.json'
        mock_configfile.return_value.__enter__.return_value = mock_file_name
        variable_file = 'test_variable_file.tfvars'

        self.tflint.init(variable_file)

        mock_init.assert_called_once_with(mock_file_name, variable_file)
        mock_configfile.assert_called_once()

    @patch(f'{pkg}tf.terraform.tflint.TFLint.execute')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.merged_args')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.executable_path',
           new_callable=PropertyMock)
    def test__init(self, mock_executable_path, mock_merged_args, mock_execute):
        mock_merged_args.return_value = [
            '--flag', '--no-color', '--config', '/mock/path/to/config.json',
            '--var-file', 'test_variable_file.tfvars']
        mock_executable_path.return_value = '/path/to/executable'
        variable_file = 'test_variable_file.tfvars'
        config_file = '/mock/path/to/config.json'
        self.tflint._init(config_file, variable_file)
        mock_merged_args.assert_called_once_with(
            self.tflint._flags,
            ['--no-color',
             '--config',
             config_file,
             '--var-file',
             variable_file]
        )

        expected_command = [
            '/path/to/executable', '--init', '--flag', '--no-color',
            '--config', config_file, '--var-file', variable_file
        ]
        mock_execute.assert_called_once_with(
            expected_command,
            self.tflint._terraform_root_module,
            self.tflint._env,
            return_output=False
        )

    @patch(f'{pkg}tf.terraform.tflint.TFLint.execute')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.merged_args')
    @patch(f'{pkg}tf.terraform.tflint.TFLint._init')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.configfile')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.executable_path',
           new_callable=PropertyMock)
    def test_tflint(self,
                    mock_executable_path,
                    mock_configfile,
                    mock_init,
                    mock_merged_args,
                    mock_execute):
        mock_executable_path.return_value = '/mock/path/to/executable'
        mock_configfile.return_value.__enter__.return_value = \
            '/mock/path/to/config.json'
        mock_init.return_value = None
        mock_merged_args.return_value = [
            '--no-color', '--config', '/mock/path/to/config.json',
            '--var-file', 'test_variable_file.tfvars']
        variable_file = 'test_variable_file.tfvars'

        self.tflint.tflint(variable_file=variable_file)
        mock_init.assert_called_once_with(
            '/mock/path/to/config.json', variable_file)
        mock_merged_args.assert_called_once_with(
            self.tflint._flags,
            ['--no-color', '--config', '/mock/path/to/config.json',
             '--var-file', variable_file]
        )

        expected_command = [
            '/mock/path/to/executable', '--no-color', '--config',
            '/mock/path/to/config.json', '--var-file', variable_file
        ]
        mock_execute.assert_called_once_with(
            expected_command,
            self.tflint._terraform_root_module,
            self.tflint._env,
            return_output=False
        )
