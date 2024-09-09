
import os

try:
    from nativeedge.exceptions import NonRecoverableError
    from nativeedge_common_sdk import hcl
    from nativeedge_common_sdk.utils import run_subprocess
    from nativeedge_common_sdk.cli_tool_base import CliTool
except ImportError:
    from cloudify.exceptions import NonRecoverableError
    from cloudify_common_sdk import hcl
    from cloudify_common_sdk.utils import run_subprocess
    from cloudify_common_sdk.cli_tool_base import CliTool


class TFTool(CliTool):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @staticmethod
    def convert_config_to_hcl(config):
        new_config_dict = dict()
        hcl_string = str()
        for cfg in config:
            data = hcl.extract_hcl_from_dict(cfg)
            if 'config' in data:
                new_config_dict.update(data['config'])
                continue
            hcl_string += hcl.convert_json_hcl(data)
        hcl_string += hcl.convert_json_hcl({'config': new_config_dict})
        return hcl_string

    @staticmethod
    def merged_args(flags, args):
        for index in range(0, len(args)):
            if args[index] not in flags:
                continue
            if args[index + 1].startswith('--'):
                continue
            flag_index = flags.index(args[index])
            args[index] = flags.pop(flag_index)
            args[index + 1] = flags.pop(flag_index + 1)
        args.extend(flags)
        return args

    def _execute(self,
                 command,
                 cwd,
                 env,
                 additional_args=None,
                 return_output=True):
        if not os.access(self.executable_path, os.X_OK):
            run_subprocess(
                ['chmod', 'u+x', self.executable_path],
                self.logger)
        return run_subprocess(
            command,
            self.logger,
            cwd,
            env,
            additional_args,
            return_output=return_output)


class TFToolException(NonRecoverableError):
    pass
