########
########
# Copyright (c) 2018-2020 GigaSpaces Technologies Ltd. All rights reserved
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

import os
import re
import json
import tempfile
from distutils.version import LooseVersion as parse_version

from .tfsec import TFSec
from .tflint import TFLint
from .terratag import Terratag
from .infracost import Infracost
from contextlib import contextmanager
from cloudify import exceptions as cfy_exc
from cloudify_common_sdk.cli_tool_base import CliTool
from cloudify_common_sdk.secure_property_management import get_stored_property
from cloudify_common_sdk.utils import (
    delete_debug,
    run_subprocess,
    update_dict_values
)

from .. import utils


CREATE_OP = 'cloudify.interfaces.lifecycle.create'


class Terraform(CliTool):
    # TODO: Rework this to put the execute method in its own module.
    # TODO: After you do that, move all the SSH commands to the tasks module.

    def __init__(self,
                 logger,
                 binary_path,
                 plugins_dir,
                 root_module,
                 variables=None,
                 environment_variables=None,
                 backend=None,
                 provider=None,
                 required_providers=None,
                 provider_upgrade=False,
                 additional_args=None,
                 version=None,
                 flags_override=None,
                 log_stdout=True,
                 tfvars=None):

        try:
            deployment_name = root_module.split('/')[-2]
            node_instance_name = root_module.split('/')[-1]
        except (IndexError, AttributeError):
            logger.info('Invalid root module: {}'.format(root_module))
            deployment_name = None
            node_instance_name = None

        super().__init__(logger, deployment_name, node_instance_name)

        backend = backend or {}
        provider = provider or {}
        variables = variables or {}
        required_providers = required_providers or {}

        self.tool_name = 'Terraform'
        self.binary_path = binary_path
        self.plugins_dir = self.set_plugins_dir(plugins_dir)
        self._root_module = root_module
        self.logger = logger
        self.additional_args = additional_args
        self._version = version
        self._flags = None
        self._flags_override = flags_override or []
        self._log_stdout = log_stdout
        self._tflint = None
        self._tfsec = None
        self._terratag = None
        self._infracost = None

        if not isinstance(environment_variables, dict):
            raise Exception(
                "Unexpected type for environment variables (should be a "
                "dict): {0}".format(type(
                    environment_variables)))

        if not isinstance(variables, dict):
            raise Exception(
                "Unexpected type for variables (should be a "
                "dict): {0}".format(type(
                    variables)))

        self._env = self.convert_bools_in_env(environment_variables)
        self._backend = backend
        self._required_providers = required_providers
        self._provider = provider
        self._variables = variables
        self.provider_upgrade = provider_upgrade
        self._tfvars = tfvars

    @property
    def root_module(self):
        return self._root_module

    @root_module.setter
    def root_module(self, value):
        self._root_module = value
        utils.try_to_copy_old_state_file(value)

    @property
    def flags(self):
        if not self._flags:
            self._flags = self._format_flags(self._flags_override)
        return self._flags

    @property
    def insecure_env(self):
        return utils.convert_secrets(self._env)

    @property
    def env(self):
        return self._env

    @env.setter
    def env(self, value):
        new_value = self.convert_bools_in_env(value)
        if self._env:
            self._env.update(new_value)
        else:
            self._env = new_value

    @property
    def insecure_variables(self):
        return utils.convert_secrets(self._variables)

    @property
    def variables(self):
        return self._variables

    @variables.setter
    def variables(self, value):
        if self._variables:
            self._variables.update(value)
        else:
            self._variables = value

    @property
    def backend(self):
        if self._backend.get('options', {}):
            return utils.create_backend_string(
                self._backend.get('name'), self._backend.get('options', {}))

    @property
    def insecure_backend(self):
        if self._backend.get('options', {}):
            insecure_backend = utils.convert_secrets(self._backend)
            return utils.create_backend_string(
                insecure_backend.get('name'),
                insecure_backend.get('options', {}))

    @property
    def required_providers(self):
        if self._required_providers.get('required_providers', {}):
            return utils.create_required_providers_string(
                self._required_providers.get('required_providers', {}))

    @property
    def insecure_required_providers(self):
        if self._required_providers.get('required_providers', {}):
            insecure_required_providers = utils.convert_secrets(
                self._required_providers)
            return utils.create_required_providers_string(
                insecure_required_providers.get('required_providers', {}))

    @property
    def provider(self):
        if self._provider.get('providers', {}):
            return utils.create_provider_string(
                self._provider.get('providers', {}))

    @property
    def insecure_provider(self):
        if self._provider.get('providers', {}):
            insecure_provider = utils.convert_secrets(
                self._provider)
            return utils.create_provider_string(
                insecure_provider.get('providers', {}))

    @staticmethod
    def convert_bools_in_env(env):
        for k, v in env.items():
            if isinstance(v, bool):
                env[k] = str(v).lower()
        return env

    @staticmethod
    def set_plugins_dir(path):
        if not os.listdir(path):
            return
        return path

    def get_valid_override_flags(self, command_args):
        def get_cleaned_flags(help_result):
            cleaned_flags = []
            # this will get us the options
            options = help_result[help_result.find('Options:'):]
            # clear any default value in flag
            for flag in re.findall(r"\s+-([^ ]+) .*", options):
                cleaned_flags.append("--{0}".format(flag.partition('=')[0]))
            return cleaned_flags

        cleaned_flags = []
        subcommand = command_args[0]
        help_result = self.execute([self.binary_path, subcommand, '-help'])
        cleaned_flags = get_cleaned_flags(help_result)
        # check if it has nested_help options
        nested_help_text = 'For more information on those options, run:'
        # get the help command by adding len of nested_help_text
        help_text_index = help_result.find(nested_help_text)
        if not cleaned_flags and help_text_index > -1:
            nested_help = help_result[help_text_index+43:]
            # replace terraform with correct binary_path
            help_command = [self.binary_path]
            help_command.extend(nested_help.split()[1:])
            help_result = self.execute(help_command)
            nested_flags = get_cleaned_flags(help_result)
            for flag in nested_flags:
                if flag not in cleaned_flags:
                    cleaned_flags.append(flag)
        # remove extra - from the formatted_flags and pick the flag
        # that is part of the supported flags by the command
        final_flags = [flag[1:] for flag in self.flags
                       if flag.partition('=')[0] in cleaned_flags]
        for flag in command_args[1:]:
            if flag not in final_flags:
                final_flags.append(flag)
        final_flags.insert(0, subcommand)
        return final_flags

    def execute(self, command, return_output=None):
        return_output = return_output if return_output is not None \
            else self._log_stdout
        self.additional_args['log_stdout'] = return_output
        if not os.access(self.binary_path, os.X_OK):
            run_subprocess(
                ['chmod', 'u+x', self.binary_path],
                self.logger)
        # look for all providers to make them executable as well
        providers_path = os.path.join(self.root_module, '.terraform')
        for things in os.walk(providers_path):
            for provider_file in things[2]:
                provider_path = os.path.join(things[0], provider_file)
                if not os.access(provider_path, os.X_OK):
                    run_subprocess(
                        ['chmod', 'u+x', provider_path],
                        self.logger)
        return run_subprocess(
            command,
            self.logger,
            self.root_module,
            self.insecure_env,
            self.additional_args,
            return_output=return_output)

    def _tf_command(self, args):
        cmd = [self.binary_path]
        if self.flags:
            flags = self.get_valid_override_flags(args)
            cmd.extend(flags)
        else:
            cmd.extend(args)
        return cmd

    def put_backend(self):
        utils.dump_file(self.insecure_backend,
                        self.root_module,
                        'backend.tf')

    def put_required_providers(self):
        utils.dump_file(self.insecure_required_providers,
                        self.root_module,
                        self._required_providers.get(
                            'filename',
                            'versions.tf.json'))

    def put_provider(self):
        utils.dump_file(self.insecure_provider,
                        self.root_module,
                        self._provider.get('filename', 'provider.tf'))

    @contextmanager
    def runtime_file(self, command):
        if self._tfvars:
            command.extend(['-var-file={}'.format(self.tfvars)])
            yield
        else:
            with tempfile.NamedTemporaryFile(suffix=".json",
                                             delete=False,
                                             mode="w",
                                             dir=self.root_module) as f:
                json.dump(self.insecure_variables, f)
                f.close()
                command.extend(['-var-file', f.name])
                yield
            if delete_debug():
                os.remove(f.name)

    @contextmanager
    def plan_file(self):
        json_result, _ = self.plan_and_show_two_formats()
        with tempfile.NamedTemporaryFile(
                'w',
                suffix='.json') as plan_file:
            plan_file.write(json.dumps(json_result))
            yield plan_file.name

    @property
    def version(self):
        if not self._version:
            returned_output = self.execute(
                self._tf_command(['version', '-json']), False)
            self._version = self.read_version(returned_output)
        return self._version

    @staticmethod
    def read_version_from_text(text):
        try:
            return re.search(
                'Terraform\\sv(.*)\\n', text.decode('utf-8')).group(1)
        except AttributeError:
            return '0.0.0'

    def read_version(self, response):
        try:
            return json.loads(response)
        except (ValueError, json.JSONDecodeError):
            return {
                'terraform_version': self.read_version_from_text(response),
                'terraform_outdated': True
            }

    @property
    def terraform_version(self):
        return self.version.get('terraform_version')

    @property
    def terraform_outdated(self):
        return self.version.get('terraform_outdated')

    @property
    def tflint(self):
        return self._tflint

    @tflint.setter
    def tflint(self, value):
        self._tflint = value

    @property
    def tfsec(self):
        return self._tfsec

    @tfsec.setter
    def tfsec(self, value):
        self._tfsec = value

    @property
    def terratag(self):
        return self._terratag

    @terratag.setter
    def terratag(self, value):
        self._terratag = value

    @property
    def infracost(self):
        return self._infracost

    @infracost.setter
    def infracost(self, value):
        self._infracost = value

    @property
    def tfvars(self):
        # os.path.join(self.root_module, self._tfvars)
        return self._tfvars

    @tfvars.setter
    def tfvars(self, value):
        self._tfvars = value

    def init(self, command_line_args=None, prefix=None, no_input=True):
        cmdline = ['init', '-no-color']
        if no_input:
            cmdline.append('-input=false')
        if self.plugins_dir:
            cmdline.append('--plugin-dir=%s' % self.plugins_dir)
        if self.provider_upgrade:
            cmdline.append('--upgrade')
        command = self._tf_command(cmdline)
        if command_line_args:
            command.extend(command_line_args)
        if prefix:
            command[:0] = prefix
        with self.runtime_file(command):
            return self.execute(command)

    def migrate_state(self, name, options, backend_config):
        migrate_args = []
        answer_yes = ['echo', 'yes', '|']
        self._backend = {
            'name': name,
            'options': options,
        }
        self.put_backend()
        for key, value in backend_config.items():
            migrate_args.append(
                '-backend-config="{key}={value}"'.format(
                    key=key, value=value))
        migrate_args.append('-migrate-state')
        self.init(migrate_args, answer_yes, no_input=False)

    def destroy(self):
        command = self._tf_command(['destroy',
                                    '-auto-approve',
                                    '-no-color',
                                    '-input=false'])
        with self.runtime_file(command):
            return self.execute(command)

    def plan(self, out_file_path=None):
        command = self._tf_command(['plan', '-no-color', '-input=false'])
        if out_file_path:
            command.extend(['-out', out_file_path])
        with self.runtime_file(command):
            return self.execute(command, False)

    def apply(self):
        command = self._tf_command(['apply',
                                    '-auto-approve',
                                    '-no-color',
                                    '-input=false'])
        with self.runtime_file(command):
            return self.execute(command)

    def output(self):
        command = self._tf_command(['output', '-json', '-no-color'])
        output = self.execute(command, False)
        if output:
            try:
                return json.loads(output)
            except json.JSONDecodeError as e:
                try:
                    cleaned_text = re.sub(r'}\s*?\n\s*?{', '}, {', output)
                    json_list_text = "[{0}]".format(cleaned_text)
                    return json.loads(json_list_text)
                except json.JSONDecodeError:
                    raise e

    def graph(self):
        command = self._tf_command(['graph'])
        return self.execute(command)

    def state_pull(self):
        command = self._tf_command(['state', 'pull'])
        pulled_state = self.execute(command, False)
        # If we got here, then the "state pull" return code must
        # be zero, and pulled_state actually contains a parse-able
        # JSON.
        if pulled_state:
            try:
                return json.loads(pulled_state)
            except json.JSONDecodeError as e:
                try:
                    cleaned_text = re.sub(r'}\s*?\n\s*?{', '}, {',
                                          pulled_state)
                    json_list_text = "[{0}]".format(cleaned_text)
                    return json.loads(json_list_text)
                except json.JSONDecodeError:
                    raise e

    def refresh(self):
        if parse_version(self.terraform_version) >= parse_version("0.15.4"):
            command = self._tf_command(['apply',
                                        '-refresh-only',
                                        '-auto-approve',
                                        '-no-color',
                                        '-input=false'])
        else:
            command = self._tf_command(['refresh', '-no-color'])
        with self.runtime_file(command):
            return self.execute(command)

    def state_list(self, plan_file_path=None):
        options = ['state', 'list']
        if plan_file_path:
            options.append('-state={}'.format(plan_file_path))
        command = self._tf_command(options)
        output = self.execute(command)
        return output

    def show(self, plan_file_path=None):
        options = ['show', '-no-color', '-json']
        if plan_file_path:
            options.append(plan_file_path)
        command = self._tf_command(options)
        output = self.execute(command, False)
        if output:
            try:
                return json.loads(output)
            except json.JSONDecodeError as e:
                try:
                    return json.loads(
                        '[' + ','.join(output.split('\n')) + ']')
                except json.JSONDecodeError:
                    raise e

    def show_plain_text(self, plan_file_path=None):
        options = ['show', '-no-color']
        if plan_file_path:
            options.append(plan_file_path)
        command = self._tf_command(options)
        return self.execute(command)

    def plan_and_show(self):
        """
        Execute terraform plan,
        then terraform show on the generated tfplan file
        """
        with tempfile.NamedTemporaryFile(delete=delete_debug()) as plan_file:
            self.plan(plan_file.name)
            return self.show(plan_file.name)

    def plan_and_show_two_formats(self):
        """
        Execute terraform plan,
        then terraform show on the generated tfplan file
        """
        with tempfile.NamedTemporaryFile() as plan_file:
            self.plan(plan_file.name)
            json_result = self.show(plan_file.name)
            plain_text_result = self.show_plain_text(plan_file.name)
            return json_result, plain_text_result

    def plan_and_show_state(self):
        """
        Execute terraform plan,
        then terraform show on the generated tfplan file
        """
        status_problems = []
        with tempfile.NamedTemporaryFile() as plan_file:
            self.plan(plan_file.name)
            plan = self.show(plan_file.name)
            self.refresh()
            for key, value in plan['planned_values']['root_module'].items():
                if key == 'resources':
                    status_problems.extend(
                        self._show_state_resource_list(value))
                elif key == 'child_modules':
                    status_problems.extend(self._show_state_of_modules(value))
        return status_problems

    def _show_state_of_modules(self, value):
        status_problems = []
        for module in value:
            if not isinstance(module, dict) or 'resources' not in module:
                continue
            status_problems.extend(self._show_state_resource_list(
                module['resources']))
        return status_problems

    def _show_state_resource_list(self, value):
        status_problems = []
        for resource in value:
            try:
                self.show_state(
                    resource['address'],
                    os.path.join(self.root_module, 'terraform.tfstate'))
            except Exception:
                status_problems.append(resource)
        return status_problems

    def show_state(self, resource_name, plan_file_path=None):
        options = ['state', 'show', '-no-color']
        if plan_file_path:
            options.append('-state={}'.format(plan_file_path))
        options.append(resource_name)
        command = self._tf_command(options)
        return self.execute(command)

    def import_resource(self, resource_address, resource_id):
        options = ['import', '-no-color']
        with self.runtime_file(options):
            options.extend([resource_address, resource_id])
            command = self._tf_command(options)
            return self.execute(command)

    @staticmethod
    def from_ctx(ctx, terraform_source, skip_tf=False, **kwargs):
        try:
            executable_path = utils.get_executable_path() or \
                              utils.get_binary_location_from_rel()
        except cfy_exc.NonRecoverableError:
            if skip_tf:
                executable_path = None
            else:
                raise
        plugins_dir = utils.get_plugins_dir()
        resource_config = utils.get_resource_config()
        provider_upgrade = utils.get_provider_upgrade()
        general_executor_process = ctx.node.properties.get(
            'general_executor_process')
        if isinstance(terraform_source, dict):
            terraform_source = terraform_source.get('location')
        if not os.path.exists(plugins_dir) and utils.is_using_existing():
            utils.mkdir_p(plugins_dir)
        env_variables = resource_config.get('environment_variables')
        terraform_version = ctx.instance.runtime_properties.get(
            'terraform_version', {})
        flags_override = resource_config.get('flags_override')
        tfvars_name_file = resource_config.get('tfvars', None)

        key_word_args = {
            'variables': resource_config.get('variables'),
            'environment_variables': env_variables or {},
            'backend': resource_config.get('backend'),
            'provider': resource_config.get('provider'),
            'required_providers': resource_config.get('required_providers'),
            'provider_upgrade': provider_upgrade,
            'additional_args': general_executor_process,
            'version': terraform_version,
            'flags_override': flags_override,
            'log_stdout': resource_config.get('log_stdout', True),
            'tfvars': tfvars_name_file,
        }
        for k in key_word_args.keys():
            if k in kwargs and kwargs[k]:
                if isinstance(kwargs[k], dict):
                    resolved_kwargs = utils.resolve_dict_intrinsic_vals(
                        kwargs[k], ctx.deployment.id)
                    if isinstance(key_word_args[k], dict):
                        key_word_args[k].update(resolved_kwargs)
                    else:
                        key_word_args[k] = kwargs[k]
                else:
                    key_word_args[k] = kwargs[k]

        tf = Terraform(
                ctx.logger,
                executable_path,
                plugins_dir,
                terraform_source,
                **key_word_args
        )
        tf.put_backend()
        tf.put_required_providers()
        tf.put_provider()
        if not terraform_version and not skip_tf:
            ctx.instance.runtime_properties['terraform_version'] = \
                tf.version
        # handle api_key property in runtime
        if 'infracost_config' in kwargs:
            result = ''
            api_key = \
                get_stored_property(ctx, 'infracost_config',
                                    force_node=True).get("api_key", {})
            infracost_config_from_params = \
                ctx.workflow_parameters.get('infracost_config', {})
            api_key_param = ''
            if isinstance(infracost_config_from_params, dict):
                api_key_param = infracost_config_from_params.get('api_key')
            # first check node property
            if api_key:
                result = \
                    utils.resolve_dict_intrinsic_vals(
                        api_key, ctx.deployment.id)
            # override that from the workflow parameter
            if api_key_param:
                result = \
                    utils.resolve_dict_intrinsic_vals(
                        api_key_param, ctx.deployment.id)
            kwargs['infracost_config']['api_key'] = result

        if 'tf' in kwargs:
            kwargs.pop('tf', None)
        setup_config_tf(ctx, tf, **kwargs)
        return tf

    def check_tflint(self):
        if not hasattr(self, 'tflint') or not self.tflint:
            return
        self.tflint.validate()
        self.tflint.terraform_root_module = self.root_module
        commands = []
        with self.runtime_file(commands):
            self.tflint.tflint(commands[-1])

    def check_tfsec(self):
        if not hasattr(self, 'tfsec') or not self.tfsec:
            return
        self.tfsec.validate()
        self.tfsec.terraform_root_module = self.root_module
        commands = []
        with self.runtime_file(commands):
            # injecting another - to make the flag correct
            if commands:
                commands[0] = "-{0}".format(commands[0])
            self.tfsec.tfsec(commands)

    def run_terratag(self):
        if not self.terratag:
            return
        self.terratag.validate()
        self.terratag.terraform_root_module = self.root_module
        commands = []
        if os.path.dirname(self.binary_path) not in os.environ['PATH']:
            os.environ['PATH'] = '{}:{}'.format(
                os.environ['PATH'], os.path.dirname(self.binary_path))
        with self.runtime_file(commands):
            self.terratag.terratag()

    def check_opa(self, decision="deny"):
        with self.plan_file() as plan_file:
            if not hasattr(self, 'opa') or not self.opa:
                return
            self.opa.validate()
            self.opa.terraform_root_module = self.root_module
            return self.opa.evaluate_policy(
                input_file=plan_file,
                decision=decision)

    def run_infracost(self):
        if not self.infracost:
            return
        self.infracost.validate()
        self.infracost.terraform_root_module = self.root_module
        if os.path.dirname(self.binary_path) not in os.environ['PATH']:
            os.environ['PATH'] = '{}:{}'.format(
                os.environ['PATH'], os.path.dirname(self.binary_path))
        return self.infracost.infracost()


def setup_config_tf(ctx,
                    tf,
                    tfsec_config=None,
                    tflint_config=None,
                    terratag_config=None,
                    infracost_config=None,
                    **_):
    if ctx.operation.name != CREATE_OP:
        if tf.terraform_outdated:
            ctx.logger.info(
                'Your terraform version {} is outdated. '
                'Please update.'.format(tf.terraform_version))

    #  TFlint
    tflint_config_from_props = ctx.node.properties.get('tflint_config', {})
    original_tflint_config = \
        ctx.instance.runtime_properties.get('tflint_config', {}) or \
        tflint_config_from_props
    new_tflint_config = update_dict_values(original_tflint_config,
                                           tflint_config)

    if tflint_config or tflint_config_from_props and \
            tflint_config_from_props.get('enable', False):
        tf.tflint = TFLint.from_ctx(_ctx=ctx, tflint_config=new_tflint_config)
        ctx.instance.runtime_properties['tflint_config'] = \
            tf.tflint.export_config()

    #  TFsec
    tfsec_config_from_props = ctx.node.properties.get('tfsec_config', {})
    original_tfsec_config = \
        ctx.instance.runtime_properties.get('tfsec_config', {}) or \
        tfsec_config_from_props
    new_tfsec_config = update_dict_values(original_tfsec_config,
                                          tfsec_config)

    if tfsec_config or tfsec_config_from_props and \
            tfsec_config_from_props.get('enable', False):
        tf.tfsec = TFSec.from_ctx(_ctx=ctx, tfsec_config=new_tfsec_config)
        ctx.instance.runtime_properties['tfsec_config'] = \
            tf.tfsec.export_config()

    # Terratag
    terratag_config_from_props = ctx.node.properties.get('terratag_config', {})
    original_terratag_config = \
        ctx.instance.runtime_properties.get('terratag_config', {}) or \
        terratag_config_from_props
    new_terratag_config = update_dict_values(
        original_terratag_config, terratag_config)

    try:
        tags_from_ctx = ctx.deployment.resource_tags
    except AttributeError:
        pass
    else:
        tags_from_cfg = new_terratag_config.get('tags', {})
        tags_from_cfg.update(tags_from_ctx)
        new_terratag_config['tags'] = tags_from_cfg

    if terratag_config or terratag_config_from_props.get('enable', False):
        tf.terratag = Terratag.from_ctx(_ctx=ctx,
                                        terratag_config=new_terratag_config)
        ctx.instance.runtime_properties['terratag_config'] = \
            tf.terratag.export_config()

    # infracost
    infracost_config_from_props = \
        ctx.node.properties.get('infracost_config', {})
    original_infracost_config = \
        ctx.instance.runtime_properties.get('infracost_config', {}) or \
        infracost_config_from_props
    new_infracost_config = update_dict_values(
        original_infracost_config, infracost_config)
    if infracost_config or infracost_config_from_props.get('enable', False):
        tf.infracost = Infracost.from_ctx(
            _ctx=ctx,
            infracost_config=new_infracost_config,
            variables=tf.variables,
            env=tf.env,
            tfvars=tf.tfvars)
        ctx.instance.runtime_properties['infracost_config'] = \
            tf.infracost.export_config()
