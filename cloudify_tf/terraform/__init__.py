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

from contextlib import contextmanager

from .. import utils


class Terraform(object):
    # TODO: Rework this to put the execute method in its own module.
    # TODO: After you do that, move all the SSH commands to the tasks module.

    def __init__(self,
                 logger,
                 binary_path,
                 plugins_dir,
                 root_module,
                 variables=None,
                 environment_variables=None,
                 provider_upgrade=False,
                 additional_args=None):

        self.binary_path = binary_path
        self.plugins_dir = self.set_plugins_dir(plugins_dir)
        self.root_module = root_module
        self.logger = logger
        self.additional_args = additional_args
        self._version = {}

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

        self.env = self.convert_bools_in_env(environment_variables)
        self.variables = variables
        self.provider_upgrade = provider_upgrade

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

    def execute(self, command, return_output=True):
        return utils.run_subprocess(
            command,
            self.logger,
            self.root_module,
            self.env,
            self.additional_args,
            return_output=return_output)

    def _tf_command(self, args):
        cmd = [self.binary_path]
        cmd.extend(args)
        return cmd

    @contextmanager
    def _vars_file(self, command):
        with tempfile.NamedTemporaryFile(suffix=".json",
                                         delete=False,
                                         mode="w",
                                         dir=self.root_module) as f:
            json.dump(self.variables, f)
            f.close()
            command.extend(['-var-file', f.name])
            yield
        os.remove(f.name)

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

    def init(self, command_line_args=None):
        cmdline = ['init', '-no-color', '-input=false']
        if self.plugins_dir:
            cmdline.append('--plugin-dir=%s' % self.plugins_dir)
        if self.provider_upgrade:
            cmdline.append('--upgrade')
        command = self._tf_command(cmdline)
        if command_line_args:
            command.extend(command_line_args)
        with self._vars_file(command):
            return self.execute(command)

    def destroy(self):
        command = self._tf_command(['destroy',
                                    '-auto-approve',
                                    '-no-color',
                                    '-input=false'])
        with self._vars_file(command):
            return self.execute(command)

    def plan(self, out_file_path=None):
        command = self._tf_command(['plan', '-no-color', '-input=false'])
        if out_file_path:
            command.extend(['-out', out_file_path])
        with self._vars_file(command):
            return self.execute(command, False)

    def apply(self):
        command = self._tf_command(['apply',
                                    '-auto-approve',
                                    '-no-color',
                                    '-input=false'])
        with self._vars_file(command):
            return self.execute(command)

    def output(self):
        command = self._tf_command(['output', '-json', '-no-color'])
        returned_output = self.execute(command, False)
        if returned_output:
            return json.loads(returned_output)

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
            return json.loads(pulled_state)

    def refresh(self):
        from distutils.version import LooseVersion as parse_version
        if parse_version(self.terraform_version) >= parse_version("0.15.4"):
            command = self._tf_command(['apply',
                                        '-refresh-only',
                                        '-auto-approve',
                                        '-no-color',
                                        '-input=false'])
        else:
            command = self._tf_command(['refresh', '-no-color'])
        with self._vars_file(command):
            return self.execute(command)

    def show(self, plan_file_path):
        command = self._tf_command(
            ['show', '-no-color', '-json', plan_file_path])
        output = self.execute(command, False)
        if output:
            return json.loads(output)

    def plan_and_show(self):
        """
        Execute terraform plan,
        then terraform show on the generated tfplan file
        """
        with tempfile.NamedTemporaryFile() as plan_file:
            self.plan(plan_file.name)
            return self.show(plan_file.name)

    @staticmethod
    def from_ctx(ctx, terraform_source):
        executable_path = utils.get_executable_path() or \
                          utils.get_binary_location_from_rel()
        plugins_dir = utils.get_plugins_dir()
        resource_config = utils.get_resource_config()
        provider_upgrade = utils.get_provider_upgrade()
        general_executor_process = ctx.node.properties.get(
            'general_executor_process')
        if not os.path.exists(plugins_dir) and utils.is_using_existing():
            utils.mkdir_p(plugins_dir)
        env_variables = resource_config.get('environment_variables')
        tf = Terraform(
                ctx.logger,
                executable_path,
                plugins_dir,
                terraform_source,
                variables=resource_config.get('variables'),
                environment_variables=env_variables,
                provider_upgrade=provider_upgrade,
                additional_args=general_executor_process)
        return tf
