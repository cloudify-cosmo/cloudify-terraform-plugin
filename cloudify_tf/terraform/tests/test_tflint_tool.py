########
########
# Copyright (c) 2018-2022 Cloudify Platform Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# import os
from pytest import fixture
from mock import MagicMock, patch
# from tempfile import mkdtemp, NamedTemporaryFile

# from cloudify.state import current_ctx
# from cloudify.mocks import MockCloudifyContext

from .. import tflint

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


@fixture
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


@fixture
def tflint_params(get_tf_tools_params):  # noqa
    args, kwargs, info, error = get_tf_tools_params
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


@patch('cloudify_common_sdk.cli_tool_base.sdk_utils')
def test_validate(sdk_utils_mock, tflint_params):
    args, kwargs, info, error = tflint_params
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
