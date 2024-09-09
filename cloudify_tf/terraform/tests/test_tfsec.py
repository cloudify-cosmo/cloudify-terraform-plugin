

import os
import shutil
import unittest
from tempfile import mkdtemp
from mock import patch, MagicMock

from cloudify.state import current_ctx
from cloudify.mocks import MockCloudifyContext

from cloudify_tf.terraform import tfsec

TFSEC_URL = 'https://github.com/aquasecurity/tfsec/' \
            'releases/download/v1.1.3/tfsec-linux-amd64'


def tfsec_params():
    logger_mock = MagicMock()
    params = {
        'logger': logger_mock,
        'deployment_name': 'foo_deployment',
        'node_instance_name': 'foo_instance',
        'installation_source': TFSEC_URL,
        'executable_path': None,
        'config': {},
        'flags_override': ['run-statistics'],
        'env': {},
        'enable': True
    }
    return params


def test_config_property_name():
    tfsec_obj = tfsec.TFSec(**tfsec_params())
    assert tfsec_obj.config_property_name == 'tfsec_config'


def test_installation_source():
    tfsec_obj = tfsec.TFSec(**tfsec_params())
    assert tfsec_obj.installation_source == TFSEC_URL


@patch('cloudify_common_sdk.cli_tool_base.sdk_utils.get_deployment_dir')
def test_executable_path(get_deployment_dir_sdk):
    ctx = MockCloudifyContext(
        'test',
        deployment_id='deployment',
        tenant={'name': 'foo'},
        properties={},
        runtime_properties={},
    )
    current_ctx.set(ctx)

    deployment_dir = mkdtemp()
    get_deployment_dir_sdk.return_value = deployment_dir
    expected_path = os.path.join(deployment_dir,
                                 tfsec_params()['node_instance_name'],
                                 'tfsec')
    os.makedirs(os.path.dirname(expected_path))
    try:
        tfsec_obj = tfsec.TFSec(**tfsec_params())
        actual_path = tfsec_obj.executable_path
        assert expected_path == actual_path
        assert os.path.isfile(actual_path)
        assert os.listdir(os.path.dirname(actual_path)) == ['tfsec']
        assert os.path.exists(actual_path)
    finally:
        shutil.rmtree(deployment_dir)


@patch('cloudify_common_sdk.cli_tool_base.sdk_utils')
def test_validation(sdk_utils_mock):
    download_file_mock = MagicMock()
    get_deployment_dir_mock = MagicMock(return_value='foo')
    sdk_utils_mock.download_file = download_file_mock
    sdk_utils_mock.get_deployment_dir = get_deployment_dir_mock
    tfsec_obj = tfsec.TFSec(**tfsec_params())
    tfsec_obj.tool_name = 'test_validate'
    tfsec_obj.validate()
    download_file_mock.assert_called_once_with(
        'foo/foo_instance', TFSEC_URL)
    assert tfsec_obj.flags == ['--run-statistics']


@patch('cloudify_tf.terraform.tfsec.NamedTemporaryFile')
@patch('cloudify_tf.terraform.tfsec.shutil.move')
def test_configfile_context_manager_with_config(mock_shutil_move,
                                                mock_named_temp_file):

    tfsec_obj = tfsec.TFSec(**tfsec_params())
    tfsec_obj._config_from_props = {'key': 'value'}
    tfsec_obj.config = {'key': 'value'}
    tfsec_obj.terraform_root_module = '/mock/root'

    mock_file = MagicMock()
    mock_named_temp_file.return_value.__enter__.return_value = mock_file

    with tfsec_obj.configfile():
        mock_named_temp_file.assert_called_once_with(mode="w+", delete=False)
        mock_file.flush.assert_called_once()
        mock_shutil_move.assert_called_once_with
        (mock_file.name, '/mock/root/config.json')


@patch('cloudify_tf.terraform.tfsec.NamedTemporaryFile')
@patch('cloudify_tf.terraform.tfsec.shutil.move')
def test_configfile_context_manager_with_exception(mock_shutil_move,
                                                   mock_named_temp_file):
    tfsec_obj = tfsec.TFSec(
        logger=MagicMock(),
        deployment_name='test_deployment',
        node_instance_name='test_node_instance'
    )
    tfsec_obj._config_from_props = {'key': 'value'}
    tfsec_obj.config = {'key': 'value'}
    tfsec_obj.terraform_root_module = '/mock/root'

    mock_file = MagicMock()
    mock_named_temp_file.return_value.__enter__.return_value = mock_file

    with unittest.TestCase.assertRaises(unittest.TestCase, Exception):
        with tfsec_obj.configfile():
            raise Exception('Test Exception')


@patch('cloudify_tf.terraform.tfsec.NamedTemporaryFile')
@patch('cloudify_tf.terraform.tfsec.shutil.move')
def test_configfile_context_manager_with_no_config(mock_shutil_move,
                                                   mock_named_temp_file):
    tfsec_obj = tfsec.TFSec(logger=MagicMock(),
                            deployment_name='test_deployment',
                            node_instance_name='test_node_instance')
    tfsec_obj.config = {}
    tfsec_obj.terraform_root_module = '/mock/root'

    mock_named_temp_file.return_value.__enter__.return_value = MagicMock()

    with tfsec_obj.configfile() as config_file:
        assert config_file is None
        mock_named_temp_file.assert_called_once()


@patch('cloudify_tf.terraform.tfsec.NamedTemporaryFile')
@patch('cloudify_tf.terraform.tfsec.shutil.move')
@patch.object(tfsec.TFSec, 'execute')
@patch('cloudify_tf.terraform.tfsec.TFSec.configfile')
@patch('cloudify_tf.terraform.tfsec.TFSec.merged_args')
@patch('cloudify_tf.terraform.tfsec.TFSec.executable_path')
def test_tfsec_basic_command(mocke_executable_path,
                             mock_merged_args,
                             mock_configfile,
                             mock_execute,
                             mock_shutil_move,
                             mock_named_temp_file):
    tfsec_obj = tfsec.TFSec(
        logger=MagicMock(),
        deployment_name='test_deployment',
        node_instance_name='test_node_instance'
    )
    tfsec_obj._config_from_props = {'key': 'value'}
    tfsec_obj.config = {'key': 'value'}
    tfsec_obj.terraform_root_module = '/mock/root'
    tfsec_obj.executable_path = '/mock/path/tfsec'
    mocke_executable_path.return_value = '/mock/path/tfsec'
    mock_named_temp_file.return_value.__enter__.return_value = MagicMock(
        name='file', spec=object)

    mock_execute.return_value = 'output'
    mock_merged_args.return_value = ['/mock/path/tfsec', '.', '--no-color',
                                     '--format', '--var-file', 'json',
                                     '--config-file', 'config.json']
    result = tfsec_obj.tfsec(command_extension=['test'])

    expected_command = ['/mock/path/tfsec', '/mock/path/tfsec', '.',
                        '--no-color', '--format', '--tfvars-file', 'json',
                        '--config-file', 'config.json']

    mock_execute.assert_called_once_with(
        expected_command, '/mock/root', {}, return_output=False
    )
    assert result == 'output'


@patch('cloudify_tf.terraform.tfsec.TFSec._execute')
def test_execute_success(mock_execute):
    mock_logger = MagicMock()
    tfsec_obj = tfsec.TFSec(
        logger=mock_logger,
        deployment_name='test_deployment',
        node_instance_name='test_node_instance'
    )

    command = ['tfsec', '--version']
    cwd = '/mock/path'
    env = {}
    return_output = True
    output = 'version 1.0.0'

    mock_execute.return_value = output

    result = tfsec_obj.execute(command, cwd, env, return_output=return_output)

    mock_logger.info.assert_any_call('command: {}'.format(command))
    mock_logger.info.assert_any_call('output: {}'.format(output))
    assert mock_execute.call_count >= 1
    assert mock_execute.call_count <= 10
    mock_execute.assert_called_with(
        command, cwd, env, {}, return_output=return_output
    )
    assert result is None


def test_instance_props_has_tfsec_config():
    node_props = {'tfsec_config': {'key': 'node_value'}}
    instance_props = {'tfsec_config': {'key': 'instance_value'}}

    result = tfsec.get_tfsec_config(node_props, instance_props)
    assert result == {'key': 'instance_value'}


def test_instance_props_does_not_have_tfsec_config():
    node_props = {'tfsec_config': {'key': 'node_value'}}
    instance_props = {}

    result = tfsec.get_tfsec_config(node_props, instance_props)
    assert result == {'key': 'node_value'}


def test_both_props_empty(self):
    node_props = {}
    instance_props = {}
    result = tfsec.get_tfsec_config(node_props, instance_props)
    assert result == {}
