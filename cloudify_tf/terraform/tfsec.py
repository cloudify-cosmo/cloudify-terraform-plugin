import json
import shutil
from os import path
from time import sleep
from contextlib import contextmanager
from tempfile import NamedTemporaryFile

from cloudify.exceptions import RecoverableError
from .tools_base import TFTool, TFToolException


class TFSec(TFTool):

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
        self._config_from_props = config
        self._config = {}
        self._flags_from_props = flags_override or []
        self._flags = []
        self._env = env or {}
        self._tool_name = 'tfsec'
        self._terraform_root_module = None
        self._enable = enable

    @property
    def config_property_name(self):
        return 'tfsec_config'

    @property
    def installation_source(self):
        return self._installation_source

    @installation_source.setter
    def installation_source(self, value):
        self._installation_source = value

    @property
    def executable_path(self):
        if self.use_system_tfsec(self.__executable_path):
            self._executable_path = self.__executable_path
        elif self.require_download_tfsec(self.__executable_path):
            self._executable_path = self.__executable_path
            self.install_binary(
                self.installation_source,
                self.node_instance_directory,
                self._executable_path,
                'tfsec'
            )
        return self._executable_path

    def require_download_tfsec(self, executable_path):
        if not executable_path or not path.isfile(executable_path):
            self.__executable_path = path.join(
                self.node_instance_directory, 'tfsec')
            return True
        return False

    def use_system_tfsec(self, executable_path):
        if not executable_path:
            # We are not using system tfsec.
            return False
        if self.node_instance_directory not in executable_path \
                and not path.isfile(executable_path):
            # We are using System TFsec and it doesn't exist.
            self._validation_errors.append(
                'A static path to a tfsec executable was provided, '
                'and the path does not exist. '
                'However, we are not able to create a file outside of the '
                'node instance directory. '
                'Either remove static executable_path, '
                'or ensure the binary is available at the provided '
                'file path, {file_path}.'.format(
                    file_path=self._executable_path)
            )
        # We are using System TFsec.
        return True

    @executable_path.setter
    def executable_path(self, value):
        self._executable_path = value

    @property
    def config(self):
        if not self._config:
            self._config = self._config_from_props
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
        executable_path = self.executable_path
        # This generates its own logs,
        # so run it 1st so that the validation messages are published together.
        self.log('Validating tfsec config.')
        self.log('Valid executable path: {executable_path}.'.format(
            executable_path=executable_path))
        self.log('Valid environment: {}'.format(self.env))
        self.log('Valid flags: {flags}'.format(flags=self.flags))
        self.log('Valid config: {config}'.format(config=self.config))
        if self._validation_errors:
            message = '\n'.join(self._validation_errors)
            raise TFSecException(
                'Validation failed. Reasons: {message}.'.format(
                    message=message))

    @property
    def flags(self):
        if not self._flags:
            self._flags = self._format_flags(self._flags_from_props)
        return self._flags

    @staticmethod
    def from_ctx(_ctx, tfsec_config=None):
        tfsec_config = tfsec_config or get_tfsec_config(
            _ctx.node.properties, _ctx.instance.runtime_properties)
        _ctx.logger.debug('Using tfsec_config {}'.format(tfsec_config))
        return TFSec(
            _ctx.logger,
            _ctx.deployment.id,
            _ctx.instance.id,
            **tfsec_config)

    @contextmanager
    def configfile(self):
        with NamedTemporaryFile(mode="w+", delete=False) as f:
            if self.config:
                json.dump(self.config, f)
                f.flush()
                shutil.move(f.name, self.terraform_root_module+'/config.json')
                try:
                    yield 'config.json'
                except Exception:
                    raise
            else:
                try:
                    yield
                except Exception:
                    raise

    def tfsec(self, command_extension=None):
        with self.configfile() as config_file:
            basic_commands = ['.', '--no-color', '--format', 'json']

            if config_file:
                basic_commands.extend(['--config-file', config_file])

            if command_extension:
                basic_commands.extend(command_extension)

            command = self.merged_args(self.flags, basic_commands)
            self.logger.debug('Swapping "var-file" with "tfvars-file", '
                              'for backward compatibility across '
                              'different tf-sec versions. '
                              'If you think this is a mistake, '
                              'please contact Cloudify support.')
            var_index = command.index('--var-file')
            command[var_index] = '--tfvars-file'
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
                            "tfsec binary is not synced yet")
                    sleep(10)
                raise TFSecException(
                    'TFsec error. See above log for more information. '
                    'If you are working in a development environment, '
                    'you may run the command, '
                    '"{}" from the directory '
                    '{} in order to replicate the plugin behavior.'.format(
                        ' '.join(command), self.terraform_root_module))
        return


def get_tfsec_config(node_props, instance_props):
    tfsec_config = instance_props.get('tfsec_config', {})
    if not tfsec_config:
        tfsec_config = node_props['tfsec_config']
    return tfsec_config


class TFSecException(TFToolException):
    pass
