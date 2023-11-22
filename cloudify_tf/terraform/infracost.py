import json
import yaml
from os import path, remove
from time import sleep
from pathlib import Path
from contextlib import contextmanager
from tempfile import NamedTemporaryFile

from cloudify.exceptions import RecoverableError
from cloudify_common_sdk.utils import install_binary

from .tools_base import TFTool, TFToolException
from ..utils import convert_secrets


class Infracost(TFTool):

    def __init__(self,
                 logger,
                 deployment_name,
                 node_instance_name,
                 installation_source=None,
                 executable_path=None,
                 api_key=None,
                 variables=None,
                 environment_variables=None,
                 tfvars=None,
                 enable=False):

        super().__init__(logger, deployment_name, node_instance_name)
        self._installation_source = installation_source
        self.__executable_path = executable_path
        self._variables = variables
        self._env = self.convert_bools_in_env(environment_variables)
        if api_key:
            self._env['INFRACOST_API_KEY'] = api_key
        self._tool_name = 'infracost'
        self._terraform_root_module = None
        self._tfvars = tfvars
        self._config = {}
        self.enable = enable

    @staticmethod
    def convert_bools_in_env(env):
        for k, v in env.items():
            if isinstance(v, bool):
                env[k] = str(v).lower()
        return env

    @property
    def config_property_name(self):
        return 'infracost_config'

    @property
    def installation_source(self):
        return self._installation_source

    @installation_source.setter
    def installation_source(self, value):
        self._installation_source = value

    @property
    def executable_path(self):
        if self.use_system_infracost(self.__executable_path):
            self._executable_path = self.__executable_path
        elif self.require_download_infracost(self.__executable_path):
            self._executable_path = self.__executable_path
            install_binary(self.node_instance_directory,
                           self._executable_path,
                           self.installation_source,
                           'infracost.tar.gz')
        return self._executable_path

    def require_download_infracost(self, executable_path):
        if not executable_path or not path.isfile(executable_path):
            self.__executable_path = path.join(
                self.node_instance_directory, 'infracost-linux-amd64')
            return True
        return False

    def use_system_infracost(self, executable_path):
        if not executable_path:
            # We are not using system infracost.
            return False
        if self.node_instance_directory not in executable_path \
                and not path.isfile(executable_path):
            # We are using System infracost and it doesn't exist.
            self._validation_errors.append(
                'A static path to an infracost executable was provided, '
                'and the path does not exist. '
                'However, we are not able to create a file outside of the '
                'node instance directory. '
                'Either remove static executable_path, '
                'or ensure the binary is available at the provided '
                'file path, {file_path}.'.format(
                    file_path=self._executable_path)
            )
        # We are using System infracost.
        return True

    @executable_path.setter
    def executable_path(self, value):
        self._executable_path = value

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
    def variables(self):
        return self._variables

    @variables.setter
    def variables(self, value):
        if self._variables:
            self._variables.update(value)
        else:
            self._variables = value

    @property
    def terraform_root_module(self):
        return self._terraform_root_module

    @terraform_root_module.setter
    def terraform_root_module(self, value):
        self._terraform_root_module = value

    @property
    def tfvars(self):
        return self._tfvars

    @tfvars.setter
    def tfvars(self, value):
        self._tfvars = value

    @property
    def config(self):
        return self._config

    @config.setter
    def config(self, value):
        self._config = value

    def validate(self):
        executable_path = self.executable_path
        # This generates its own logs,
        # so run it 1st so that the validation messages are published together.
        self.log('Validating infracost config.')
        self.log('Valid executable path: {executable_path}.'.format(
            executable_path=executable_path))
        self.log('Valid environment: {}'.format(self.env))
        self.log('Valid config: {config}'.format(config=self.config))
        if self._validation_errors:
            message = '\n'.join(self._validation_errors)
            raise InfracostException(
                'Validation failed. Reasons: {message}.'.format(
                    message=message))

    @staticmethod
    def from_ctx(_ctx, infracost_config=None, variables=None, env=None,
                 tfvars=None):
        infracost_config = infracost_config or get_infracost_config(
            _ctx.node.properties, _ctx.instance.runtime_properties)
        infracost_config['variables'] = variables
        infracost_config['environment_variables'] = env
        infracost_config['tfvars'] = tfvars
        return Infracost(
            _ctx.logger,
            _ctx.deployment.id,
            _ctx.instance.id,
            **infracost_config)

    @contextmanager
    def runtime_file(self):
        if self._tfvars:
            yield self.tfvars
        else:
            with NamedTemporaryFile(suffix=".json",
                                    delete=False,
                                    mode="w",
                                    dir=self.terraform_root_module) as f:
                json.dump(convert_secrets(self.variables), f)
                f.close()
                yield f.name
            remove(f.name)

    @contextmanager
    def config_file(self):
        with NamedTemporaryFile(suffix=".yaml",
                                delete=False,
                                mode="w",
                                dir=self.terraform_root_module) as f:
            yaml.dump(self.config, f)
            f.close()
            yield f.name
        remove(f.name)

    def infracost(self):
        result = ''
        json_result = '{}'
        command = [self.executable_path, 'breakdown', '--config-file']
        with self.runtime_file() as f:
            root_path = str(Path(self.terraform_root_module).resolve())
            root_path = root_path.replace('/.', '')
            self.config = {
                'version': 0.1,
                'projects': [{
                    'path': root_path,
                    'terraform_var_files': [f],
                    'env': convert_secrets(self.env)
                }]
            }
            with self.config_file() as cf:
                command.extend([cf, '--show-skipped', '--no-color'])
                result = self.execute(command, self.terraform_root_module,
                                      convert_secrets(self.env),
                                      return_output=True)
                # injecting the executable_path because execute pops item 0
                command.insert(0, self.executable_path)
                command.remove('--show-skipped')
                command.extend(['--format json'])
                json_result = self.execute(command, self.terraform_root_module,
                                           convert_secrets(self.env),
                                           return_output=True)
        return result, json.loads(json_result)

    def export_config(self):
        return {
            'installation_source': self.installation_source,
            'executable_path': self.executable_path,
            'variables': self.variables,
            'environment_variables': self.env,
            'tfvars': self.tfvars,
        }

    def execute(self, command, cwd, env, return_output=True, *args, **kwargs):
        for n in range(0, 10):
            try:
                return self._execute(
                    command, cwd, env, kwargs, return_output=return_output)
            except Exception as e:
                if 'No such file or directory' in str(e):
                    if n == 10:
                        raise RecoverableError(
                            "infracost binary is not synced yet")
                    sleep(10)
                raise InfracostException(
                    'Infracost error. See above log for more information. '
                    'If you are working in a development environment, '
                    'you may run the command, '
                    '"{}" from the directory '
                    '{} in order to replicate the plugin behavior.'.format(
                        ' '.join(command), self.terraform_root_module))
        return


def get_infracost_config(node_props, instance_props):
    infracost_config = instance_props.get('infracost_config', {})
    if not infracost_config:
        infracost_config = node_props['infracost_config']
    return infracost_config


class InfracostException(TFToolException):
    pass
