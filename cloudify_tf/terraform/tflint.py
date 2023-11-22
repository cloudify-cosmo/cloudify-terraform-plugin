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

from os import path, remove
from time import sleep
from contextlib import contextmanager
from tempfile import NamedTemporaryFile

from cloudify.exceptions import RecoverableError
from .tools_base import TFTool, TFToolException

SUPPORTED_CONFIGS = [
    'config',
    'plugin',
    'rule',
    'variables',
    'varfile',
    'ignore_module',
    'disabled_by_default',
    'force',
    'module',
    'plugin_dir'
]


class TFLint(TFTool):

    def __init__(self,
                 logger,
                 deployment_name,
                 node_instance_name,
                 installation_source=None,
                 executable_path=None,
                 config=None,
                 flags_override=None,
                 env=None,
                 enable=False):

        super().__init__(logger, deployment_name, node_instance_name)
        self._installation_source = installation_source
        self.__executable_path = executable_path
        self._config_from_props = config or [
            {
                'type_name': 'config',
                'option_value': {
                    'module': 'true'
                }
            }
        ]
        self._config = {}
        self._flags_from_props = flags_override or []
        self._flags = []
        self._env = env or {}
        self._tool_name = 'tflint'
        self._terraform_root_module = None
        self._enable = enable

    @property
    def config_property_name(self):
        return 'tflint_config'

    @property
    def installation_source(self):
        return self._installation_source

    @installation_source.setter
    def installation_source(self, value):
        self._installation_source = value

    @property
    def executable_path(self):
        if self.use_system_tflint(self.__executable_path):
            self._executable_path = self.__executable_path
        elif self.require_download_tflint(self.__executable_path):
            self._executable_path = self.__executable_path
            self.install_binary(
                self.installation_source,
                self.node_instance_directory,
                self._executable_path,
                'tflint.zip'
            )
        return self._executable_path

    def require_download_tflint(self, executable_path):
        if not executable_path or not path.isfile(executable_path):
            self.__executable_path = path.join(
                self.node_instance_directory, 'tflint')
            return True
        return False

    def use_system_tflint(self, executable_path):
        if not executable_path:
            # We are not using system tflint.
            return False
        if self.node_instance_directory not in executable_path \
                and not path.isfile(executable_path):
            # We are using System TFlint and it doesn't exist.
            self._validation_errors.append(
                'A static path to a tflint executable was provided, '
                'and the path does not exist. '
                'However, we are not able to create a file outside of the '
                'node instance directory. '
                'Either remove static executable_path, '
                'or ensure the binary is available at the provided '
                'file path, {file_path}.'.format(
                    file_path=self._executable_path)
            )
        # We are using System TFLint.
        return True

    @executable_path.setter
    def executable_path(self, value):
        self._executable_path = value

    @property
    def config(self):
        if not self._config:
            self._config = self._format_config()
        return self._config

    @config.setter
    def config(self, value):
        self._config_from_props = value

    @config.setter
    def config(self, value):
        self._flags_from_props = value

    @property
    def env(self):
        return self._env

    @env.setter
    def env(self, value):
        self._env = value

    @property
    def terraform_root_module(self):
        return self._terraform_root_module

    @terraform_root_module.setter
    def terraform_root_module(self, value):
        self._terraform_root_module = value

    def validate(self):
        executable_path = self.executable_path  # This generates its own logs,
        # so run it 1st so that the validation messages are published together.
        self.log('Validating tflint config.')
        self.log('Valid executable path: {executable_path}.'.format(
            executable_path=executable_path))
        self.log('Valid environment: {}'.format(self.env))
        self.log('Valid flags: {flags}'.format(flags=self.flags))
        self.log('Valid config: {config}'.format(config=self.config))
        if self._validation_errors:
            message = '\n'.join(self._validation_errors)
            raise TFLintException(
                'Validation failed. Reasons: {message}.'.format(
                    message=message))

    @property
    def flags(self):
        if not self._flags:
            self._flags = self._format_flags(self._flags_from_props)
        return self._flags

    def _format_config(self):
        errors = []
        for config in self._config_from_props:
            type_name = config['type_name']
            if type_name not in SUPPORTED_CONFIGS:
                errors.append(
                    'Config option {type_name} is not supported.'.format(
                        type_name=type_name)
                )
                continue
        self._validation_errors.extend(errors)
        return self.convert_config_to_hcl(self._config_from_props)

    @staticmethod
    def from_ctx(_ctx, tflint_config=None):
        tflint_config = tflint_config or get_tflint_config(
            _ctx.node.properties, _ctx.instance.runtime_properties)
        _ctx.logger.debug('Using tflint_config {}'.format(tflint_config))
        return TFLint(
            _ctx.logger,
            _ctx.deployment.id,
            _ctx.instance.id,
            **tflint_config)

    @contextmanager
    def configfile(self):
        with NamedTemporaryFile(
                dir=self.terraform_root_module, delete=False) as tflint_cfg:
            tflint_cfg.write(self.config.encode('utf-8'))
            tflint_cfg.flush()
            try:
                yield tflint_cfg.name
            except (Exception, FileNotFoundError) as e:
                self.logger.error(
                    'Not attempting to delete config file for '
                    'troubleshooting {}'.format(tflint_cfg.name))
                raise TFLintException('Issue running TFLint: {}'.format(
                    str(e)))
            if path.exists(tflint_cfg.name):
                try:
                    remove(tflint_cfg.name)
                except FileNotFoundError:
                    pass

    def init(self, variable_file):
        with self.configfile() as tflint_cfg:
            self._init(tflint_cfg, variable_file)

    def _init(self, config_file, variable_file):
        basic_commands = ['--no-color', '--config', config_file]
        if variable_file:
            basic_commands.extend(['--var-file', variable_file])
        command = self.merged_args(
            self.flags, basic_commands)
        command.insert(0, '--init')
        command.insert(0, self.executable_path)
        return self.execute(command, self.terraform_root_module, self.env,
                            return_output=False)

    def tflint(self, variable_file=None):
        with self.configfile() as config_file:
            self._init(config_file, variable_file)
            basic_commands = ['--no-color', '--config', config_file]
            if variable_file:
                basic_commands.extend(['--var-file', variable_file])
            command = self.merged_args(
                self.flags, basic_commands)
            command.insert(0, self.executable_path)
            return self.execute(command, self.terraform_root_module, self.env,
                                return_output=False)

    def export_config(self):
        return {
            'installation_source': self.installation_source,
            'executable_path': self.executable_path,
            'config': self._config_from_props,
            'flags_override': self._flags_from_props,
            'env': self.env,
        }

    def execute(self, command, cwd, env, return_output=True, *args, **kwargs):
        for n in range(0, 10):
            try:
                self.logger.info('command: {}'.format(command))
                output = self._execute(
                    command, cwd, env, kwargs, return_output=return_output)
                self.logger.info('output: {}'.format(output))
            except Exception as e:
                if 'No such file or directory' in str(e):
                    if n == 10:
                        raise RecoverableError(
                            "tflint binary is not synced yet")
                    sleep(10)
                raise TFLintException(
                    'TFLint error. See above log for more information. '
                    'If you are working in a development environment, '
                    'you may run the command, '
                    '"{}" from the directory '
                    '{} in order to replicate the plugin behavior.'.format(
                        ' '.join(command), self.terraform_root_module))
        return


def get_tflint_config(node_props, instance_props):
    tflint_config = instance_props.get('tflint_config', {})
    if not tflint_config:
        tflint_config = node_props['tflint_config']
    return tflint_config


class TFLintException(TFToolException):
    pass
