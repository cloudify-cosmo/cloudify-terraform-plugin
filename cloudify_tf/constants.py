
NAME = 'name'
STATE = 'state'
DRIFTS = 'drifts'
IS_DRIFTED = 'is_drifted'
MASKED_ENV_VARS = {
    'AWS_ACCESS_KEY_ID',
    'AWS_SECRET_ACCESS_KEY'
}
TERRAFORM_STATE_FILE = 'terraform.tfstate'
HCL_DICT_TEMPLATE = """{name} {{
{indent}{value}
{indent}{indent}}}
"""
HCL_STR_TEMPLATE = '{indent}{name} = "{value}"\n'
