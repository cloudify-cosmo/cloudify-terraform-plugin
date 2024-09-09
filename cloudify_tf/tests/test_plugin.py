# Copyright © 2024 Dell Inc. or its subsidiaries. All Rights Reserved.

import logging
from os import path
from mock import (
    Mock,
    patch,
    MagicMock
)
from tempfile import mkdtemp
from contextlib import contextmanager

from cloudify.exceptions import OperationRetry
from cloudify.state import current_ctx
from cloudify.mocks import (MockContext,
                            MockNodeContext,
                            MockCloudifyContext as MockcloudifyContext,
                            MockNodeInstanceContext)

from . import TestBase
from ..tasks import (apply,
                     install,
                     check_drift,
                     migrate_state,
                     setup_linters,
                     import_resource,
                     _reload_template,
                     set_directory_config,
                     compare_plan_results,
                     FailedPlanValidation)

from ..utils import RELATIONSHIP_INSTANCE
from ..terraform import (
    Terraform,
    ProcessException
)

test_dir1 = mkdtemp()
test_dir2 = mkdtemp()
test_dir3 = mkdtemp()
pkg = 'cloudify_'


class MockcloudifyContextRels(MockcloudifyContext):

    @property
    def type(self):
        return RELATIONSHIP_INSTANCE


class TestPlugin(TestBase):

    def setUp(self):
        super(TestPlugin, self).setUp()

    def get_terraform_conf_props(self, module_root):
        return {
            "terraform_config": {
                "executable_path": path.join(module_root, "terraform"),
                "storage_path": module_root,
                "plugins_dir": path.join(
                    module_root, '.terraform', "plugins"),
            },
            "resource_config": {
                "use_existing_resource": False,
                "installation_source":
                    "https://releases.hashicorp.com/terraform/0.11.7/"
                    "terraform_0.11.7_linux_amd64.zip",
                "plugins": {}
            }
        }

    def get_terraform_module_conf_props(self, module_root):
        return {
            "terratag_config": {
                "installation_source": "https://github.com/env0/terratag/"
                                       "releases/download/v0.1.35/"
                                       "terratag_0.1.35_linux_amd64.tar.gz",
                "executable_path": False,
                "tags": {},
                "flags_override": [],
                "enable": False,
            },
            "resource_config": {
                "source": {
                    "location": path.join(module_root, "template"),
                },
                "variables": {
                    "a": "var1",
                    "b": "var2"
                },
                "environment_variables": {
                    "EXEC_PATH": path.join(module_root, "execution"),
                }
            },
        }

    @patch(f'{pkg}tf.tasks.get_node_instance_dir',
           return_value=test_dir1)
    @patch(f'{pkg}tf.utils.get_node_instance_dir',
           return_value=test_dir1)
    @patch(f'{pkg}common_sdk.utils.run_subprocess')
    @patch(f'{pkg}common_sdk.utils.os.remove')
    @patch(f'{pkg}common_sdk.utils.unzip_and_set_permissions')
    @patch(f'{pkg}common_sdk.utils.install_binary', suffix='tf.zip')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_install(self, mock_resource_config, *_):
        conf = self.get_terraform_conf_props(test_dir1)
        mock_resource_config.return_value = conf.get('resource_config')
        ctx = self.mock_ctx("test_install", conf)
        current_ctx.set(ctx=ctx)
        kwargs = {
            'ctx': ctx
        }
        install(**kwargs)
        self.assertEqual(
            ctx.instance.runtime_properties.get("executable_path"),
            conf.get("terraform_config").get("executable_path"))
        self.assertEqual(
            ctx.instance.runtime_properties.get("storage_path"),
            conf.get("terraform_config").get("storage_path"))
        self.assertEqual(
            ctx.instance.runtime_properties.get("plugins_dir"),
            conf.get("terraform_config").get("plugins_dir"))

    @patch('os.path.exists')
    @patch(f'{pkg}tf.utils.get_node_instance_dir',
           return_value=test_dir2)
    @patch(f'{pkg}tf.tasks.get_node_instance_dir',
           return_value=test_dir2)
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_set_directory_config(self, mock_resource_config, *_):
        target = MockContext({
            'instance': MockNodeInstanceContext(
                id='terra_install-1',
                runtime_properties=self.get_terraform_conf_props(
                    test_dir2).get("terraform_config")
            ),
            'node': MockNodeContext(
                id='1',
                properties=self.get_terraform_conf_props(test_dir2)
            ), '_context': {
                'node_id': '1'
            }})
        source_work_dir = mkdtemp()
        conf = self.get_terraform_module_conf_props(source_work_dir)
        source = MockContext({
            'instance': MockNodeInstanceContext(
                id='terra_module-1',
                runtime_properties={}),
            'node': MockNodeContext(
                id='2',
                properties=conf
            ), '_context': {
                'node_id': '2'
            }})
        ctx = MockcloudifyContextRels(source=source, target=target)
        current_ctx.set(ctx=ctx)
        kwargs = {
            'ctx': ctx
        }
        mock_resource_config.return_value = conf
        set_directory_config(**kwargs)
        self.assertEqual(
            ctx.source.instance.runtime_properties.get("executable_path"),
            ctx.target.instance.runtime_properties.get("executable_path")
        )

    @patch(f'{pkg}tf.utils._unzip_archive')
    @patch(f'{pkg}tf.utils.copy_directory')
    @patch(f'{pkg}tf.utils.get_terraform_state_file', return_value=False)
    @patch(f'{pkg}tf.utils.get_ne_version', return_value="6.1.0")
    @patch(f'{pkg}tf.utils.get_node_instance_dir',
           return_value=test_dir3)
    @patch(f'{pkg}tf.terraform.Terraform.terraform_outdated',
           return_value=False)
    @patch(f'{pkg}tf.utils.store_sensitive_properties')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_apply_no_output(self, mock_resource_config, *_):
        conf = self.get_terraform_module_conf_props(test_dir3)
        mock_resource_config.return_value = conf.get('resource_config')
        ctx = self.mock_ctx("test_apply_no_output", conf)
        current_ctx.set(ctx=ctx)
        kwargs = {
            'ctx': ctx
        }

        tf_pulled_resources = {'resources': [{'name': 'eip',
                                              'value': '10.0.0.1'}]}
        tf_output = {}
        mock_tf_apply = Mock()
        mock_tf_apply.init.return_value = 'terraform initialized folder'
        mock_tf_apply.plan.return_value = 'terraform plan'
        mock_tf_apply.apply.return_value = 'terraform executing'
        mock_tf_apply.state_pull.return_value = tf_pulled_resources
        mock_tf_apply.show.return_value = tf_pulled_resources
        mock_tf_apply.output.return_value = tf_output

        with patch(f'{pkg}tf.terraform.Terraform.from_ctx',
                   return_value=mock_tf_apply):
            apply(**kwargs)
            self.assertTrue(mock_tf_apply.show.called)
            self.assertEqual(ctx.instance.runtime_properties['resources'],
                             {'eip': tf_pulled_resources.get('resources')[0]})
            self.assertEqual(ctx.instance.runtime_properties['outputs'],
                             tf_output)

    @patch(f'{pkg}tf.utils._unzip_archive')
    @patch(f'{pkg}tf.utils.copy_directory')
    @patch(f'{pkg}tf.utils.get_terraform_state_file', return_value=False)
    @patch(f'{pkg}tf.utils.get_ne_version', return_value="6.1.0")
    @patch(f'{pkg}tf.utils.get_node_instance_dir',
           return_value=test_dir3)
    @patch(f'{pkg}tf.utils.store_sensitive_properties')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_apply_with_output(self, mock_resource_config, *_):
        conf = self.get_terraform_module_conf_props(test_dir3)
        ctx = self.mock_ctx("test_apply_with_output", conf)
        mock_resource_config.return_value = conf.get('resource_config')
        current_ctx.set(ctx=ctx)
        kwargs = {
            'ctx': ctx
        }

        tf_pulled_resources = {'resources': [{'name': 'eip',
                                              'value': '10.0.0.1'}]}
        tf_output = tf_output = {'elastic_ip': {
            'sensitive': False,
            'type': 'string',
            'value': '10.0.0.1'
        }}
        mock_tf_apply = Mock()
        mock_tf_apply.init.return_value = 'terraform initialized folder'
        mock_tf_apply.plan.return_value = 'terraform plan'
        mock_tf_apply.apply.return_value = 'terraform executing'
        mock_tf_apply.state_pull.return_value = tf_pulled_resources
        mock_tf_apply.show.return_value = tf_pulled_resources
        mock_tf_apply.output.return_value = tf_output

        with patch(f'{pkg}tf.terraform.Terraform.from_ctx',
                   return_value=mock_tf_apply):
            apply(**kwargs)
            self.assertTrue(mock_tf_apply.show.called)
            self.assertEqual(ctx.instance.runtime_properties['resources'],
                             {'eip': tf_pulled_resources.get('resources')[0]})
            self.assertEqual(ctx.instance.runtime_properties['outputs'],
                             tf_output)

    @patch(f'{pkg}tf.utils._unzip_archive')
    @patch(f'{pkg}tf.utils.copy_directory')
    @patch(f'{pkg}tf.utils.get_terraform_state_file', return_value=False)
    @patch(f'{pkg}tf.utils.get_ne_version', return_value="6.1.0")
    @patch(f'{pkg}tf.utils.get_node_instance_dir',
           return_value=test_dir3)
    @patch(f'{pkg}common_sdk.utils.get_rest_client')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_apply_with_sensitive_output(self, mock_resource_config, *_):
        conf = self.get_terraform_module_conf_props(test_dir3)
        conf['resource_config']['obfuscate_sensitive'] = True
        ctx = self.mock_ctx("test_apply_with_sensitive_output", conf)
        mock_resource_config.return_value = conf.get('resource_config')
        current_ctx.set(ctx=ctx)
        kwargs = {
            'ctx': ctx
        }

        tf_pulled_resources = {'resources': [{'name': 'eip',
                                              'value': '10.0.0.1'}]}
        tf_output = {'elastic_ip': {
            'sensitive': True,
            'type': 'string',
            'value': '10.0.0.1'
        }}

        mock_tf_apply = Mock()
        mock_tf_apply.init.return_value = 'terraform initialized folder'
        mock_tf_apply.plan.return_value = 'terraform plan'
        mock_tf_apply.apply.return_value = 'terraform executing'
        mock_tf_apply.state_pull.return_value = tf_pulled_resources
        mock_tf_apply.show.return_value = tf_pulled_resources
        mock_tf_apply.output.return_value = tf_output

        mock_secrets = Mock()
        mock_secrets.create.return_value = 'secret created'
        client_mock = Mock()
        client_mock.secrets = mock_secrets

        tf_output_obfuscated = {'elastic_ip': '*' * 10}
        with patch(f'{pkg}tf.utils.with_rest_client',
                   return_value=client_mock):
            with patch(f'{pkg}tf.terraform.Terraform.from_ctx',
                       return_value=mock_tf_apply):
                apply(**kwargs)
                self.assertTrue(mock_tf_apply.show.called)
                self.assertEqual(
                    ctx.instance.runtime_properties['resources'],
                    {'eip': tf_pulled_resources.get('resources')[0]})
                self.assertEqual(ctx.instance.runtime_properties['outputs'],
                                 tf_output_obfuscated)
                client_mock.secrets.create.assert_not_called()

    @patch(f'{pkg}common_sdk.utils.get_deployment_dir')
    @patch(f'{pkg}tf.terraform.terratag.Terratag.execute')
    @patch(f'{pkg}tf.terraform.terratag.Terratag.executable_path')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.version')
    @patch(f'{pkg}tf.utils.get_executable_path')
    @patch(f'{pkg}tf.utils.get_plugins_dir')
    @patch(f'{pkg}common_sdk.utils.install_binary', suffix='tf.zip')
    @patch(f'{pkg}tf.utils.dump_file')
    @patch(f'{pkg}tf.utils.store_sensitive_properties')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_env_vars(self, mock_resource_config, *_):
        conf = self.get_terraform_module_conf_props(test_dir3)
        conf['resource_config']['environment_variables'] = {   # noqa
            'true': True,
            'false': False}
        mock_resource_config.return_value = conf.get('resource_config')

        ctx = self.mock_ctx("test_apply_with_output", conf)
        current_ctx.set(ctx=ctx)
        t = Terraform.from_ctx(ctx, 'foo')
        self.assertEqual(t.env, {'true': 'true', 'false': 'false'})
        t.env = {'null': 'null'}
        self.assertEqual(t.env,
                         {'true': 'true', 'false': 'false', 'null': 'null'})

    @patch(f'{pkg}tf.terraform.tools_base.TFTool.install_binary')
    @patch(f'{pkg}tf.terraform.Terraform.version')
    @patch(f'{pkg}tf.terraform.utils.get_binary_location_from_rel')
    @patch(f'{pkg}tf.decorators.get_terraform_source')
    @patch(f'{pkg}tf.utils.store_sensitive_properties')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.validate')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.export_config')
    @patch(f'{pkg}tf.terraform.tfsec.TFSec.validate')
    @patch(f'{pkg}tf.terraform.tfsec.TFSec.export_config')
    @patch(f'{pkg}tf.terraform.terratag.Terratag.validate')
    @patch(f'{pkg}tf.terraform.terratag.Terratag.export_config')
    @patch(f'{pkg}common_sdk.utils.get_deployment_dir')
    @patch(f'{pkg}tf.utils.get_node_instance_dir')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_setup_linters(self,
                           mock_resource_config,
                           mock_node_dir,
                           mock_dep_dir,
                           mock_terratag_export,
                           mock_terratag_validate,
                           mock_tfsec_export,
                           mock_tfsec_validate,
                           mock_tflint_export,
                           mock_tflint_validate,
                           *_):
        conf = self.get_terraform_module_conf_props(test_dir3)
        conf.update({
            "tflint_config": {
                'installation_source': 'installation_source_foo',
                'executable_path': 'executable_path_foo',
                'config': [
                    {
                        'type_name': 'plugin',
                        'option_name': 'bar',
                        'option_value': {
                            'baz': 'taco'
                        }
                    }
                ],
                'flags_override': ['foo'],
                'env': {
                    'foo': 'bar'
                },
                'enable': True
            },
            "tfsec_config": {
                'installation_source': 'installation_source_tfsec',
                'executable_path': 'executable_path_tfsec',
                'config': {},
                'flags_override': [],
                'env': {},
                'enable': True
            },
            "terratag_config": {
                'installation_source': 'installation_source_terratag',
                'executable_path': 'executable_path_terratag',
                'tags': {'tag1: value1'},
                'flags_override': [],
                'env': {},
                'enable': True
            },
        })
        mock_resource_config.return_value = conf
        ctx = self.mock_ctx("test_apply_with_output", conf)
        ctx.instance._id = 'foo'
        current_ctx.set(ctx=ctx)
        mock_node_dir.return_value = mkdtemp()
        mock_dep_dir.return_value = mkdtemp()
        setup_linters(ctx=ctx)
        mock_terratag_validate.assert_called_once()
        self.assertEqual(mock_terratag_export.call_count, 2)
        mock_tfsec_validate.assert_called_once()
        self.assertEqual(mock_tfsec_export.call_count, 2)
        mock_tflint_validate.assert_called_once()
        self.assertEqual(mock_tflint_export.call_count, 2)

    @patch(f'{pkg}tf.terraform.terratag.Terratag.execute')
    @patch(f'{pkg}tf.terraform.Terraform.init')
    @patch(f'{pkg}tf.terraform.Terraform.plan_and_show')
    @patch(f'{pkg}tf.terraform.Terraform.apply')
    @patch(f'{pkg}tf.terraform.Terraform.show')
    @patch(f'{pkg}tf.terraform.Terraform.output')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.validate')
    @patch(f'{pkg}tf.terraform.tools_base.TFTool.execute')
    @patch(f'{pkg}tf.utils.get_terraform_state_file', return_value=False)
    @patch(f'{pkg}tf.utils.get_ne_version', return_value="6.1.0")
    @patch(f'{pkg}tf.terraform.tools_base.TFTool.install_binary')
    @patch(f'{pkg}tf.terraform.Terraform.version')
    @patch(f'{pkg}tf.terraform.utils.get_binary_location_from_rel')
    @patch(f'{pkg}tf.decorators.get_terraform_source')
    @patch(f'{pkg}tf.utils.store_sensitive_properties')
    @patch(f'{pkg}tf.terraform.Terraform.runtime_file')
    @patch(f'{pkg}common_sdk.utils.get_deployment_dir')
    @patch(f'{pkg}tf.utils.get_node_instance_dir')
    @patch(f'{pkg}tf.terraform.tflint.TFLint.tflint')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_apply_check_tflint(self,
                                mock_resource_config,
                                mock_tflint,
                                mock_node_dir,
                                mock_dep_dir,
                                mock_runtime_file,
                                *_):
        conf = self.get_terraform_module_conf_props(test_dir3)
        mock_resource_config.return_value = conf.get('resource_config')
        conf.update({
            "tflint_config": {
                'installation_source': 'installation_source_foo',
                'executable_path': 'executable_path_foo',
                'config': [
                    {
                        'type_name': 'plugin',
                        'option_name': 'bar',
                        'option_value': {
                            'baz': 'taco'
                        }
                    }
                ],
                'flags_override': ['foo'],
                'env': {
                    'foo': 'bar'
                },
                'enable': True
            },
        })
        ctx = self.mock_ctx("test_apply_with_output", conf)
        ctx.instance._id = 'foo'
        current_ctx.set(ctx=ctx)

        @contextmanager
        def runtime_file(command, *args, **kwargs):
            command.extend(['-var-file', Mock()])
            yield

        mock_runtime_file.side_effect = runtime_file

        mock_node_dir.return_value = mkdtemp()
        mock_dep_dir.return_value = mkdtemp()
        apply(ctx=ctx)
        mock_tflint.assert_called()

    @patch(f'{pkg}tf.terraform.terratag.Terratag.execute')
    @patch(f'{pkg}tf.terraform.Terraform.init')
    @patch(f'{pkg}tf.terraform.Terraform.plan_and_show')
    @patch(f'{pkg}tf.terraform.Terraform.apply')
    @patch(f'{pkg}tf.terraform.Terraform.show')
    @patch(f'{pkg}tf.terraform.Terraform.output')
    @patch(f'{pkg}tf.terraform.tfsec.TFSec.validate')
    @patch(f'{pkg}tf.terraform.tools_base.TFTool.execute')
    @patch(f'{pkg}tf.utils.get_terraform_state_file', return_value=False)
    @patch(f'{pkg}tf.utils.get_ne_version', return_value="6.1.0")
    @patch(f'{pkg}tf.terraform.tools_base.TFTool.install_binary')
    @patch(f'{pkg}tf.terraform.Terraform.version')
    @patch(f'{pkg}tf.terraform.utils.get_binary_location_from_rel')
    @patch(f'{pkg}tf.decorators.get_terraform_source')
    @patch(f'{pkg}tf.utils.store_sensitive_properties')
    @patch(f'{pkg}tf.terraform.Terraform.runtime_file')
    @patch(f'{pkg}common_sdk.utils.get_deployment_dir')
    @patch(f'{pkg}tf.utils.get_node_instance_dir')
    @patch(f'{pkg}tf.terraform.tfsec.TFSec.tfsec')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_apply_check_tfsec(self,
                               mock_resource_config,
                               mock_tfsec,
                               mock_node_dir,
                               mock_dep_dir,
                               mock_runtime_file,
                               *_):
        conf = self.get_terraform_module_conf_props(test_dir3)
        mock_resource_config.return_value = conf.get('resource_config')
        conf.update({
            "tfsec_config": {
                'installation_source': 'installation_source_tfsec',
                'executable_path': 'executable_path_tfsec',
                'config': {},
                'flags_override': [],
                'env': {},
                'enable': True
            },
        })
        ctx = self.mock_ctx("test_apply_with_output", conf)
        ctx.instance._id = 'foo'
        current_ctx.set(ctx=ctx)
        mock_node_dir.return_value = mkdtemp()
        mock_dep_dir.return_value = mkdtemp()
        apply(ctx=ctx)
        mock_tfsec.assert_called()

    @patch(f'{pkg}tf.utils._unzip_archive')
    @patch(f'{pkg}tf.utils.copy_directory')
    @patch(f'{pkg}tf.utils.get_terraform_state_file', return_value=False)
    @patch(f'{pkg}tf.utils.get_ne_version', return_value="6.1.0")
    @patch(f'{pkg}tf.utils.get_node_instance_dir',
           return_value=test_dir3)
    @patch(f'{pkg}tf.terraform.Terraform.terraform_outdated',
           return_value=False)
    @patch(f'{pkg}tf.utils.store_sensitive_properties')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_check_drift(self, mock_resource_config, *_):
        conf = self.get_terraform_module_conf_props(test_dir3)
        mock_resource_config.return_value = conf.get('resource_config')
        ctx = self.mock_ctx("test_check_drift", conf)
        current_ctx.set(ctx=ctx)
        kwargs = {
            'ctx': ctx
        }
        resource_name = "example_vpc"
        vpc_change = {
            "actions": ["foo"],
            "before": {
                "arn": "fake_arn",
                "cidr_block": "10.10.0.0/16"
            },
            "after": {
                "arn": "fake_arn",
                "cidr_block": "10.10.0.0/16"
            },
            "after_unknown": {}
        }
        mock_plan_and_show = {
            "format_version": "0.1",
            "terraform_version": "0.13.4",
            "variables": {},
            "planned_values": {},
            "resource_changes": [
                {
                    "address": "aws_vpc.example_vpc",
                    "mode": "managed",
                    "type": "aws_vpc",
                    "name": resource_name,
                    "provider_name": "registry.terraform.io/hashicorp/aws",
                    "change": vpc_change
                }
            ],
            "prior_state": {},
            "configuration": {}
        }
        tf_pulled_resources = {
            'resources': [
                {
                    'name': 'eip',
                    'value': '10.0.0.1'
                }
            ]
        }
        tf_output = {}
        mock_tf_apply = Mock()
        mock_tf_apply.init.return_value = 'terraform initialized folder'
        mock_tf_apply.plan.return_value = 'terraform plan'
        mock_tf_apply.apply.return_value = 'terraform executing'
        mock_tf_apply.state_pull.return_value = tf_pulled_resources
        mock_tf_apply.show.return_value = tf_pulled_resources
        mock_tf_apply.output.return_value = tf_output
        mock_tf_apply.plan_and_show.return_value = mock_plan_and_show

        with patch(f'{pkg}tf.terraform.Terraform.from_ctx',
                   return_value=mock_tf_apply):
            check_drift(**kwargs)
            ctx.abort_operation.assert_called()

    @patch(f'{pkg}tf.terraform.terratag.Terratag.executable_path')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.Terraform.version')
    @patch(f'{pkg}tf.terraform.utils.get_executable_path')
    @patch(f'{pkg}tf.terraform.utils.get_plugins_dir')
    @patch(f'{pkg}tf.terraform.utils.get_provider_upgrade')
    @patch(f'{pkg}tf.utils.store_sensitive_properties')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_apply_tf_vars(self, mock_resource_config, *_):
        _conf = self.get_terraform_module_conf_props(test_dir3)
        conf = {
            "terratag_config": _conf['terratag_config'],
            "resource_config": {
                "tfvars": 'val.tfvars'
            }
        }
        mock_resource_config.return_value = conf.get('resource_config')
        tfvars_mock = 'val.tfvars'
        key_word_args = {
            'tfvars': tfvars_mock,
        }
        conf['resource_config']['tfvars'] = tfvars_mock
        ctx = self.mock_ctx("test_tfvars", conf)
        tf = Terraform.from_ctx(ctx, 'foo', **key_word_args)

        def fake_func():
            command = ['start']
            with tf.runtime_file(command):
                return command

        result = fake_func()
        expected = '-var-file={}'.format(tfvars_mock)
        self.assertTrue(expected in result)

    @patch(f'{pkg}tf.utils._unzip_archive')
    @patch(f'{pkg}tf.utils.copy_directory')
    @patch(f'{pkg}tf.utils.get_terraform_state_file', return_value=False)
    @patch(f'{pkg}tf.utils.get_ne_version', return_value="6.1.0")
    @patch(f'{pkg}tf.utils.get_node_instance_dir',
           return_value=test_dir3)
    @patch(f'{pkg}tf.terraform.Terraform.terraform_outdated',
           return_value=False)
    @patch(f'{pkg}tf.utils.store_sensitive_properties')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_import_resource(self, mock_resource_config, *_):
        conf = self.get_terraform_module_conf_props(test_dir3)
        mock_resource_config.return_value = conf.get('resource_config')
        ctx = self.mock_ctx("test_import_resource", conf)
        current_ctx.set(ctx=ctx)
        kwargs = {
            'ctx': ctx,
            'resource_address': 'aws_instance.example_vm',
            'resource_id': 'i-06e504391884deb3c'
        }
        mock_plan_and_show = {
            "format_version": "0.1",
            "terraform_version": "0.13.4",
            "variables": {},
            "planned_values": {},
            "resource_changes": [],
            "prior_state": {},
            "configuration": {}
        }

        tf_pulled_resources = {
            'resources': [{
                'name': 'example_vm',
                'value': {
                    "mode": "managed",
                    "type": "aws_instance",
                    "name": "example_vm",
                }
            }
            ]
        }
        tf_output = {}
        mock_tf_import = Mock()
        mock_tf_import.init.return_value = 'terraform initialized folder'
        mock_tf_import.plan.return_value = 'terraform plan'
        mock_tf_import.import_resource.return_value = 'Import successful!'
        mock_tf_import.state_pull.return_value = tf_pulled_resources
        mock_tf_import.output.return_value = tf_output
        mock_tf_import.plan_and_show.return_value = mock_plan_and_show

        with patch(f'{pkg}tf.terraform.Terraform.from_ctx',
                   return_value=mock_tf_import):
            import_resource(**kwargs)
            self.assertTrue(mock_tf_import.import_resource.called)
            self.assertEqual(
                ctx.instance.runtime_properties['resources'],
                {'example_vm': tf_pulled_resources.get('resources')[0]})
            self.assertEqual(ctx.instance.runtime_properties['outputs'],
                             tf_output)

    @patch(f'{pkg}tf.terraform.utils.get_binary_location_from_rel')
    @patch(f'{pkg}tf.terraform.Terraform.runtime_file')
    @patch(f'{pkg}tf.terraform.Terraform.version')
    @patch(f'{pkg}tf.decorators.get_terraform_source')
    @patch(f'{pkg}common_sdk.utils.get_deployment_dir')
    @patch(f'{pkg}tf.utils.get_plugins_dir')
    @patch(f'{pkg}tf.utils.dump_file')
    @patch(f'{pkg}tf.utils.store_sensitive_properties')
    @patch(f'{pkg}tf.utils._unzip_archive')
    @patch(f'{pkg}tf.utils.copy_directory')
    @patch(f'{pkg}tf.utils.get_terraform_state_file', return_value=False)
    @patch(f'{pkg}tf.utils.get_ne_version', return_value="6.1.0")
    @patch(f'{pkg}tf.utils.get_node_instance_dir',
           return_value=test_dir3)
    @patch(f'{pkg}tf.terraform.Terraform.terraform_outdated',
           return_value=False)
    @patch(f'{pkg}tf.utils.store_sensitive_properties')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.utils.get_executable_path')
    @patch(f'{pkg}tf.terraform.Terraform.execute')
    @patch(f'{pkg}tf.utils.get_resource_config')
    def test_migrate_state(self,
                           mock_resource_config,
                           mock_execute,
                           mock_exec_path,
                           mock_plugins_dir,
                           *_):
        conf = self.get_terraform_module_conf_props(test_dir3)
        mock_resource_config.return_value = conf.get('resource_config')
        mock_exec_path.return_value = 'terraform'
        mock_plugins_dir.return_value = 'foo'
        ctx = self.mock_ctx("test_migrate_state", conf)
        current_ctx.set(ctx=ctx)
        backend = {
            'name': 'foo',
            'options': {
                'bar': 'baz'
            }
        }
        backend_config = {
            'bar': 'baz'
        }
        kwargs = dict(ctx=ctx,
                      backend=backend,
                      backend_config=backend_config)
        migrate_state(**kwargs)
        mock_execute.assert_called_with([
            'echo',
            'yes',
            '|',
            'terraform',
            'init',
            '-no-color',
            "--plugin-dir=foo",
            '-backend-config="bar=baz"',
            '-migrate-state']
        )

    @patch(f'{pkg}tf.terraform.os')
    @patch(f'{pkg}tf.terraform.Terraform.set_plugins_dir')
    @patch(f'{pkg}tf.terraform.run_subprocess',
           side_effect=ProcessException(
               'foo',
               2,
               None,
               'panic: runtime error: invalid memory address '
               'or nil pointer dereference'))
    def test_execute_process_exception(self,
                                       mock_os,
                                       mock_set_dir,
                                       mock_run,
                                       *_):
        mock_os.access.return_value = True
        mock_os.walk.return_value = []
        logger = logging.getLogger('test_execute_process_exception')
        t = Terraform(
            logger,
            'foo',
            test_dir1,
            '/qux/foo/bar/bash',
            {},
            {},
            additional_args={}
        )
        with self.assertRaises(OperationRetry):
            t.execute('foo', False)

    @patch(f'{pkg}tf.tasks.DeepDiff')
    @patch(f'{pkg}tf.tasks.ctx_from_imports')
    def test_compare_plan_results_diff(self,
                                       mock_ctx_from_imports,
                                       mock_DeepDiff):
        mock_logger = logging.getLogger()
        mock_ctx_from_imports.logger = mock_logger

        new_plan = {
            'resource_changes': [{'address': 'resource1'},
                                 {'address': 'resource2'}]
        }
        old_plan = {
            'resource_changes': [{'address': 'resource1'}]
        }

        mock_DeepDiff.return_value = {'difference': 'some_diff'}

        with self.assertRaises(FailedPlanValidation):
            compare_plan_results(new_plan, old_plan)

    @patch(f'{pkg}tf.tasks.DeepDiff')
    @patch(f'{pkg}tf.tasks.ctx_from_imports')
    def test_compare_plan_results_no_diff(self,
                                          mock_ctx_from_imports,
                                          mock_DeepDiff):
        mock_logger = logging.getLogger()
        mock_ctx_from_imports.logger = mock_logger

        new_plan = {
            'resource_changes': [{'address': 'resource1'},
                                 {'address': 'resource2'}]
        }
        old_plan = {
            'resource_changes': [{'address': 'resource2'},
                                 {'address': 'resource1'}]
        }
        mock_DeepDiff.return_value = {}
        # No exception should be raised
        try:
            compare_plan_results(new_plan, old_plan)
        except FailedPlanValidation:
            self.fail("compare_plan_results raised FailedPlanValidation \
                      unexpectedly!")

    @patch(f'{pkg}tf.tasks._handle_new_vars')
    @patch(f'{pkg}tf.tasks.utils.get_resource_config')
    @patch(f'{pkg}tf.tasks.utils.handle_previous_source_format')
    @patch(f'{pkg}tf.tasks.destroy')
    @patch(f'{pkg}tf.tasks.utils.update_terraform_source')
    @patch(f'{pkg}tf.tasks._apply')
    @patch(f'{pkg}tf.tasks.utils.update_resource_config')
    @patch(f'{pkg}tf.tasks._state_pull')
    @patch(f'{pkg}tf.tasks.utils.get_terraform_state_file')
    def test_reload_template(self, mock_get_terraform_state_file,
                             mock_update_resource_config,
                             mock_state_pull,
                             mock_apply,
                             mock_update_terraform_source,
                             mock_destroy,
                             mock_handle_previous_source_format,
                             mock_get_resource_config,
                             mock_handle_new_vars):
        mock_ctx = MagicMock()
        mock_tf = MagicMock()
        mock_resource_config = {
            'source': 'default_source',
            'source_path': 'default_path'
        }

        mock_get_resource_config.return_value = mock_resource_config
        mock_handle_previous_source_format.return_value = 'formatted_source'
        mock_get_terraform_state_file.return_value = 'state_file'

        _reload_template(
            ctx=mock_ctx,
            tf=mock_tf,
            source='new_source',
            source_path='new_path',
            variables={'var': 'value'},
            environment_variables={'env_var': 'env_value'},
            destroy_previous=True,
            force=True
        )

        mock_handle_new_vars.assert_called_once_with(
            mock_ctx.instance.runtime_properties,
            mock_tf,
            {'var': 'value'},
            {'env_var': 'env_value'},
            update=True
        )
        mock_get_resource_config.assert_called_once()
        mock_handle_previous_source_format.assert_called_once_with(
            'new_source')
        mock_destroy.assert_called_once_with(tf=mock_tf, ctx=mock_ctx)
        mock_update_terraform_source.assert_called_once_with(
            'formatted_source', 'new_path', mock_tf)
        mock_apply.assert_called_once_with(
            mock_tf, mock_ctx.instance.runtime_properties.get('plan'), True
        )
        mock_update_resource_config.assert_called()
        mock_state_pull.assert_called_once()
        mock_get_terraform_state_file.assert_called_once_with(
            mock_tf.root_module)
