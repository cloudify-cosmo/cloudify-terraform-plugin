
import os
import shutil
import unittest

from tempfile import mkdtemp
from mock import MagicMock, patch

from cloudify.state import current_ctx
from cloudify.mocks import MockCloudifyContext

from .. import terratag

TERRATAG_URL = 'https://github.com/env0/terratag/releases/download/v0.1.35/' \
            'terratag_0.1.35_linux_amd64.tar.gz'

ctx = MockCloudifyContext(
        'test',
        deployment_id='deployment',
        tenant={'name': 'tenant_test'},
        properties={},
        runtime_properties={},
    )


def terratag_params():
    logger_mock = MagicMock()
    params = {
        'logger': logger_mock,
        'deployment_name': 'foo_deployment',
        'node_instance_name': 'foo_instance',
        'installation_source': TERRATAG_URL,
        'executable_path': None,
        'tags': {'tag1: value1'},
        'flags_override': ['verbose=True', 'rename=False'],
        'env': {},
        'enable': True
    }
    return params


def test_terratag_property_name():
    terratag_obj = terratag.Terratag(**terratag_params())
    assert terratag_obj.config_property_name == 'terratag_config'


def test_installation_source():
    terratag_obj = terratag.Terratag(**terratag_params())
    assert terratag_obj.installation_source == TERRATAG_URL


@patch('cloudify_common_sdk.utils.get_deployment_dir')
def test_executable_path(get_deployment_dir_sdk):
    current_ctx.set(ctx)
    deployment_dir = mkdtemp()
    get_deployment_dir_sdk.return_value = deployment_dir
    expected_path = os.path.join(deployment_dir,
                                 terratag_params()['node_instance_name'],
                                 'terratag')
    os.makedirs(os.path.dirname(expected_path))
    try:
        terratag_obj = terratag.Terratag(**terratag_params())
        actual_path = terratag_obj.executable_path
        assert expected_path == actual_path
        assert os.path.isfile(actual_path)
        assert os.path.exists(actual_path)
    finally:
        shutil.rmtree(deployment_dir)


@patch('cloudify_common_sdk.utils.get_deployment_dir')
def test_validate(get_deployment_dir_sdk):
    current_ctx.set(ctx)
    deployment_dir = mkdtemp()
    get_deployment_dir_sdk.return_value = deployment_dir
    expected_path = os.path.join(deployment_dir,
                                 terratag_params()['node_instance_name'],
                                 'terratag')
    os.makedirs(os.path.dirname(expected_path))
    try:
        terratag_obj = terratag.Terratag(**terratag_params())
        terratag_obj.validate()
    finally:
        shutil.rmtree(deployment_dir)
    assert terratag_obj.tags == {'tag1: value1'}
    assert terratag_obj.flags == ['-verbose=True', '-rename=False']


class TestTerratag(unittest.TestCase):

    def setUp(self):
        self.logger = MagicMock()
        self.deployment_name = 'test_deployment'
        self.node_instance_name = 'test_node_instance'
        self.executable_path = '/path/to/executable'
        self.config_file = '/path/to/config_file'
        self.variable_file = '/path/to/variable_file'
        self.tf_root_module = '/path/to/terraform_root_module'
        self.env = {'key': 'value'}

        self.terratag = terratag.Terratag(
            self.logger,
            self.deployment_name,
            self.node_instance_name,
            installation_source='source_url',
            executable_path='/path/to/executable',
            tags={'tag': 'test'},
            flags_override=['--flag'],
            env={'key': 'value'},
            enable=True,
            terraform_executable=None
            )

    def test_default_values(self):
        self.assertEqual(self.terratag.installation_source,
                         'source_url')
        self.assertEqual(self.terratag.flags, ['-rename=False'])
        self.assertEqual(self.terratag.env, {'key': 'value'})
        self.assertEqual(self.terratag.config_property_name,
                         'terratag_config')
        self.assertIsNone(self.terratag.terraform_root_module)

    @patch('cloudify_tf.terraform.terratag.Terratag.use_system_terratag')
    @patch('cloudify_tf.terraform.terratag.Terratag.require_download_terratag')
    def test_setters(self,
                     mock_use_system_terratag,
                     mock_require_download_terratag):
        mock_use_system_terratag.return_value = False
        mock_require_download_terratag.return_value = False

        self.terratag.installation_source = 'installation_source_new'
        self.terratag.terraform_executable = 'terraform_executable_new'
        self.terratag.executable_path = 'executable_path_new'
        self.assertEqual(self.terratag.terraform_executable,
                         'terraform_executable_new')
        self.assertEqual(self.terratag.installation_source,
                         'installation_source_new')
        self.assertEqual(self.terratag.executable_path,
                         'executable_path_new')

    @patch('cloudify_tf.terraform.terratag.Terratag.use_system_terratag')
    def test_executable_path(self, mock_use_system_terratag):
        mock_use_system_terratag.return_value = True
        result = self.terratag.executable_path
        self.assertEqual(result, '/path/to/executable')

    @patch('os.path.isfile')
    def test_require_download_terratag(self, mock_isfile):
        mock_isfile.return_value = True
        result = self.terratag.require_download_terratag('/path/to/executable')
        self.assertFalse(result)

    def test_flags_string(self):
        result = self.terratag.flags_string
        self.assertEqual(result, '-rename=False')

    def test_tags(self):
        self.terratag.tags = {'tag': 'new'}
        self.assertEqual(self.terratag.tags, {'tag': 'new'})

    def test_tags_string(self):
        self.terratag.tags_string = {'tags_string': 'new'}
        self.assertEqual(self.terratag.tags_string, {'tags_string': 'new'})

    def test_env(self):
        self.terratag.env = {'key': 'new'}
        self.assertEqual(self.terratag.env, {'key': 'new'})

    def test_terraform_root_module(self):
        self.terratag.terraform_root_module = 'new/path/terraform_root_module'
        self.assertEqual(self.terratag.terraform_root_module,
                         'new/path/terraform_root_module')

    @patch('cloudify_tf.terraform.terratag.Terratag.use_system_terratag')
    @patch('cloudify_tf.terraform.terratag.Terratag.execute')
    def test_terratag(self, mock_execute, mock_use_system_terratag):
        mock_use_system_terratag.return_value = True
        self.terratag.terratag()
        mock_execute.assert_called()

    def test_get_terratag_config(self):
        node_props = {'foo': 'bar'}
        instance_props = {'terratag_config': {'foo': 'instance_props'}}
        result = terratag.get_terratag_config(node_props, instance_props)
        self.assertEqual(result, {'foo': 'instance_props'})

        node_props = {'terratag_config': {'foo': 'node_props'}}
        instance_props = {}
        result = terratag.get_terratag_config(node_props, instance_props)
        self.assertEqual(result, {'foo': 'node_props'})
