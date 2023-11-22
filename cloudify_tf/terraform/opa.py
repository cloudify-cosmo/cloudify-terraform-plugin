import json
import shutil
from os import path
from time import sleep
from contextlib import contextmanager
from tempfile import NamedTemporaryFile

from cloudify.exceptions import RecoverableError
from .tools_base import TFTool, TFToolException
from cloudify_common_sdk.utils import get_node_instance_dir


class Opa(TFTool):

    def __init__(self,
                 logger,
                 deployment_name,
                 node_instance_name,
                 installation_source=None,
                 executable_path=None,
                 config=None,
                 flags_override=None,
                 env=None,
                 policy_bundles=None,
                 enable=False):

        super().__init__(logger, deployment_name, node_instance_name)
        self._installation_source = installation_source
        self.__executable_path = executable_path
        self._config_from_props = config
        self._config = {}
        self._flags_from_props = flags_override or []
        self._flags = []
        self._env = env or {}
        self._tool_name = 'opa'
        self._terraform_root_module = None
        self.policy_bundles = policy_bundles
        self._enable = enable

    @property
    def config_property_name(self):
        return 'opa_config'

    @property
    def installation_source(self):
        return self._installation_source

    @installation_source.setter
    def installation_source(self, value):
        self._installation_source = value

    @property
    def executable_path(self):
        if self.use_system_opa(self.__executable_path):
            self._executable_path = self.__executable_path
        elif self.require_download_opa(self.__executable_path):
            self._executable_path = self.__executable_path
            self.install_binary(
                self.installation_source,
                self.node_instance_directory,
                self._executable_path,
                'opa'
            )
        return self._executable_path

    def require_download_opa(self, executable_path):
        if not executable_path or not path.isfile(executable_path):
            self.__executable_path = path.join(
                self.node_instance_directory, 'opa')
            return True
        return False

    def use_system_opa(self, executable_path):
        if not executable_path:
            # We are not using system opa.
            return False
        if self.node_instance_directory not in executable_path \
                and not path.isfile(executable_path):
            # We are using System OPA and it doesn't exist.
            self._validation_errors.append(
                'A static path to an OPA executable was provided, '
                'and the path does not exist. '
                'However, we are not able to create a file outside of the '
                'node instance directory. '
                'Either remove static executable_path, '
                'or ensure the binary is available at the provided '
                'file path, {file_path}.'.format(
                    file_path=self._executable_path)
            )
        # We are using System OPA.
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
        self.log('Validating OPA config.')
        self.log('Valid executable path: {executable_path}.'.format(
            executable_path=executable_path))
        self.log('Valid environment: {}'.format(self.env))
        self.log('Valid flags: {flags}'.format(flags=self.flags))
        self.log('Valid config: {config}'.format(config=self.config))
        if self._validation_errors:
            message = '\n'.join(self._validation_errors)
            raise OpaException(
                'Validation failed. Reasons: {message}.'.format(
                    message=message))

    @property
    def flags(self):
        if not self._flags:
            self._flags = self._format_flags(self._flags_from_props)
        return self._flags

    @staticmethod
    def from_ctx(_ctx, opa_config=None):
        opa_config = opa_config or get_opa_config(
            _ctx.node.properties, _ctx.instance.runtime_properties)
        _ctx.logger.debug('Using opa_config {}'.format(opa_config))
        return Opa(
            _ctx.logger,
            _ctx.deployment.id,
            _ctx.instance.id,
            **opa_config)

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

    # Evaluate OPA policies against a given input file, such as a Terraform
    # plan. The decision specified by the decision kwarg is evaluated, and the
    # JSON evaluation result is returned
    def evaluate_policy(self, input_file=None, decision="deny",
                        command_extension=None):

        if not input_file:
            raise OpaException("No input file defined for OPA to execute"
                               " against.")

        with self.configfile() as config_file:
            basic_commands = ['exec']

            basic_commands.extend(['--decision', decision])

            # Bundles are stored as subdirectories within the module node
            # instance's directory. Add each to the list of bundles consulted
            # by OPA
            node_instance_dir = get_node_instance_dir()
            for bundle in self.policy_bundles:
                bundle_path = path.join(node_instance_dir, bundle['name'])
                self.logger.debug("Adding bundle at {} to OPA bundle list"
                                  .format(bundle_path))
                basic_commands.extend(['--bundle', bundle_path])

            if config_file:
                basic_commands.extend(['--config-file', config_file])

            if command_extension:
                basic_commands.extend(command_extension)

            basic_commands.append(input_file)

            command = self.merged_args(self.flags, basic_commands)

            command.insert(0, self.executable_path)
            result = self.execute(command,
                                  self.terraform_root_module,
                                  self.env,
                                  return_output=True)
            return self.parse_result(result)

    # Parses the JSON result returned by OPA to determine if a policy
    # evaluation was successful. Returns a boolean indicating the policy
    # evaluation and the JSON response from OPA.
    def parse_result(self, result):
        result = json.loads(result)
        evaluation_passed = True

        # OPA will return a list of evaluation results for each evaluated
        # bundle. If any of these are defined (len > 0), then we consider
        # the evaluation to have failed. Future work may want to extned this
        # to let the user choose an allow vs. deny result.
        try:
            for evaluation in result['result']:
                self.logger.debug("Eval result: {}".format(type(evaluation)))
                for eval_result in evaluation['result']:
                    if len(eval_result) > 0:
                        evaluation_passed = False
        except KeyError:
            # A KeyError is "normal" if the evaluation passes because the
            # result will be undefined.
            pass
        except Exception as e:
            # Other exceptions are abnormal and we should log them
            self.logger.error(
                "Exception while trying to parse OPA result: {}"
                .format(str(e)))
            evaluation_passed = False

        return evaluation_passed, result

    def export_config(self):
        return {
            'installation_source': self.installation_source,
            'executable_path': self.executable_path,
            'config': self._config_from_props,
            'flags_override': self._flags_from_props,
            'env': self.env,
            'policy_bundles': self.policy_bundles,
        }

    def execute(self, command, cwd, env, return_output=True, *args, **kwargs):
        for n in range(0, 10):
            try:
                self.logger.info('command: {}'.format(command))
                output = self._execute(
                    command, cwd, env, kwargs, return_output=return_output)
                self.logger.info('output: {}'.format(output))
                return output
            except Exception as e:
                if 'No such file or directory' in str(e):
                    if n == 10:
                        raise RecoverableError(
                            "opa binary is not synced yet")
                    sleep(10)
                raise OpaException(
                    'OPA error. See above log for more information. '
                    'If you are working in a development environment, '
                    'you may run the command, '
                    '"{}" from the directory '
                    '{} in order to replicate the plugin behavior.'.format(
                        ' '.join(command), self.terraform_root_module))


def get_opa_config(node_props, instance_props):
    opa_config = instance_props.get('opa_config', {})
    if not opa_config:
        opa_config = node_props['opa_config']
    return opa_config


class OpaException(TFToolException):
    pass
