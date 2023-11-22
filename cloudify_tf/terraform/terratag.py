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

import re
import json
from os import path
from time import sleep

from cloudify.exceptions import RecoverableError
from cloudify_common_sdk.utils import install_binary

from .tools_base import TFTool, TFToolException

SUPPORTED_FLAGS = [
    'dir',  # defaults .. .tf file
    'skipTerratagFiles',  # Default to false. Skips any previously tagged
    'verbose',  # defaults true. Turn on verbose logging
    'rename',  # defaults false. Instead of replacing files named <basename>.tf
    'filter'  # defaults to .*.
]


class Terratag(TFTool):

    def __init__(self,
                 logger,
                 deployment_name,
                 node_instance_name,
                 installation_source=None,
                 executable_path=None,
                 tags=None,
                 flags_override=None,
                 env=None,
                 enable=False,
                 terraform_executable=None):

        super().__init__(logger, deployment_name, node_instance_name)
        self._installation_source = installation_source
        self.__executable_path = executable_path
        self._tags_from_props = tags or {}
        self._tags = {}
        self._tags_string = ''
        self._flags_from_props = flags_override or []
        self._flags = []
        self._env = env or {}
        self._tool_name = 'terratag'
        self._terraform_root_module = None
        self.enable = enable
        self._terraform_executable = terraform_executable

    @property
    def config_property_name(self):
        return 'terratag_config'

    @property
    def installation_source(self):
        return self._installation_source

    @installation_source.setter
    def installation_source(self, value):
        self._installation_source = value

    @property
    def terraform_executable(self):
        return self._terraform_executable

    @terraform_executable.setter
    def terraform_executable(self, value):
        self._terraform_executable = value

    @property
    def executable_path(self):
        if self.use_system_terratag(self.__executable_path):
            self._executable_path = self.__executable_path
        elif self.require_download_terratag(self.__executable_path):
            self._executable_path = self.__executable_path
            install_binary(self.node_instance_directory,
                           self._executable_path,
                           self.installation_source,
                           'terratag.tar.gz')
        return self._executable_path

    def require_download_terratag(self, executable_path):
        if not executable_path or not path.isfile(executable_path):
            self.__executable_path = path.join(
                self.node_instance_directory, 'terratag')
            return True
        return False

    def use_system_terratag(self, executable_path):
        if not executable_path:
            # We are not using system terratag.
            return False
        if self.node_instance_directory not in executable_path \
                and not path.isfile(executable_path):
            # We are using System Terratag and it doesn't exist.
            self._validation_errors.append(
                'A static path to a terratag executable was provided, '
                'and the path does not exist. '
                'However, we are not able to create a file outside of the '
                'node instance directory. '
                'Either remove static executable_path, '
                'or ensure the binary is available at the provided '
                'file path, {file_path}.'.format(
                    file_path=self._executable_path)
            )
        # We are using System Terratag.
        return True

    @executable_path.setter
    def executable_path(self, value):
        self._executable_path = value

    @property
    def flags(self):
        if not self._flags:
            self._flags = self._format_flags(self._flags_from_props)
            for index in range(0, len(self._flags)):
                self._flags[index] = self._flags[index].replace('--', '-')
            if '-rename=False' not in self._flags:
                self._flags.append('-rename=False')
        return self._flags

    @property
    def flags_string(self):
        return ' '.join(self.flags)

    @flags.setter
    def flags(self, value):
        self._flags_from_props = value

    @property
    def tags(self):
        if not self._tags:
            self._tags = self._tags_from_props
        return self._tags

    @tags.setter
    def tags(self, value):
        self._tags = value

    @property
    def tags_string(self):
        if not self._tags_string:
            self._tags_string = '-tags={}'.format(repr(json.dumps(self.tags)))
        return self._tags_string

    @tags_string.setter
    def tags_string(self, value):
        self._tags_string = value

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
        self.log('Validating terratag config.')
        self.log('Valid executable path: {executable_path}.'.format(
            executable_path=executable_path))
        self.log('Valid environment: {}'.format(self.env))
        self.log('Valid flags: {flags}'.format(flags=self.flags))

        for flag in self._flags:
            sub_flag = re.findall('-(.*?)=', flag)
            if len(sub_flag) > 0:
                if sub_flag[0] not in SUPPORTED_FLAGS:
                    self._validation_errors.append(
                        '{flag} is not supported in flag overrides.'
                        .format(flag=flag))

        self.log('Valid tags: {config}'.format(config=self.tags))

        if self._validation_errors:
            message = '\n'.join(self._validation_errors)
            raise TerratagException('Validation failed. Reasons: {message}.'
                                    .format(message=message))

    @staticmethod
    def from_ctx(_ctx, terratag_config=None):
        terratag_config = terratag_config or get_terratag_config(
            _ctx.node.properties, _ctx.instance.runtime_properties)
        _ctx.logger.debug('Using terratag_config {}'.format(terratag_config))
        return Terratag(
            _ctx.logger,
            _ctx.deployment.id,
            _ctx.instance.id,
            **terratag_config)

    def terratag(self):
        command = [self.executable_path,
                   self.tags_string,
                   self.flags_string]
        return self.execute(command, self._terraform_root_module, self.env,
                            return_output=False)

    def export_config(self):
        return {
            'installation_source': self.installation_source,
            'executable_path': self.executable_path,
            'tags': self.tags,
            'flags_override': self._flags_from_props,
            'env': self.env,
        }

    def execute(self, command, cwd, env, return_output=True, *args, **kwargs):
        for n in range(0, 10):
            try:
                self._execute(
                    command, cwd, env, kwargs, return_output=return_output)
            except Exception as e:
                if 'No such file or directory' in str(e):
                    if n == 10:
                        raise RecoverableError(
                            "terratag binary is not synced yet")
                    sleep(10)
                raise TerratagException(
                    'Terratag error. See above log for more information. '
                    'If you are working in a development environment, '
                    'you may run the command, '
                    '"{}" from the directory '
                    '{} in order to replicate the plugin behavior.'.format(
                        ' '.join(command), self.terraform_root_module))
        return


def get_terratag_config(node_props, instance_props):
    terratag_config = instance_props.get('terratag_config', {})
    if not terratag_config:
        terratag_config = node_props['terratag_config']
    return terratag_config


class TerratagException(TFToolException):
    pass
