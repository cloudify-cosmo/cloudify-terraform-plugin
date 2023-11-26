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
import os

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
