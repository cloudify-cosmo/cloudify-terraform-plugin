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
import sys

from deepdiff import DeepDiff
from cloudify.decorators import operation
from cloudify import ctx as ctx_from_imports
from cloudify.utils import exception_to_error_cause
from cloudify.exceptions import NonRecoverableError, RecoverableError
from cloudify_common_sdk.utils import (
    install_binary,
    update_dict_values,
    get_node_instance_dir)

from . import utils
from ._compat import mkdir_p
from .constants import IS_DRIFTED
from .decorators import (
    with_terraform,
    skip_if_existing)
from .terraform.opa import Opa
from .terraform.tfsec import TFSec
from .terraform.tflint import TFLint
from .terraform.terratag import Terratag
from .terraform.infracost import Infracost
from .terraform.tools_base import TFToolException


@operation
@with_terraform
def terratag(ctx, tf, terratag_config, **_):
    original_tflint_config = ctx.instance.runtime_properties.get(
        'terratag_config') or ctx.node.properties.get('terratag_config')
    new_terratag_config = update_dict_values(
        original_tflint_config, terratag_config)
    tf.terratag = Terratag.from_ctx(ctx, new_terratag_config)
    tf.run_terratag()
    resource_config = utils.get_resource_config()
    source = resource_config.get('source')
    source_path = resource_config.get('source_path')
    _reload_template(ctx,
                     tf,
                     source,
                     source_path,
                     **_)
    ctx.instance.runtime_properties['terratag_config'] = \
        tf.terratag.export_config()


@operation
@with_terraform
def tflint(ctx, tf, tflint_config, **_):
    original_tflint_config = ctx.instance.runtime_properties.get(
        'tflint_config') or ctx.node.properties.get('tflint_config')
    new_config_tflint = update_dict_values(
        original_tflint_config, tflint_config)
    tf.tflint = TFLint.from_ctx(ctx, new_config_tflint)
    tf.check_tflint()
    ctx.instance.runtime_properties['tflint_config'] = \
        tf.tflint.export_config()


@operation
@with_terraform
def evaluate_opa_policy(ctx, tf, opa_config, decision, **_):

    # Ensure Terraform is initialized and obtain a current Terraform plan
    tf.init()
    tf.plan_and_show()

    # Setup the OPA config and store in the runtime props
    original_opa_config = ctx.instance.runtime_properties.get(
        'opa_config') or ctx.node.properties.get('opa_config')
    new_opa_config = update_dict_values(
        original_opa_config, opa_config)
    tf.opa = Opa.from_ctx(ctx, new_opa_config)
    ctx.instance.runtime_properties['opa_config'] = \
        tf.opa.export_config()

    # Download and extract OPA policy bundles to the TF module's subdirectory
    utils.get_opa_bundles()

    # Evaluate the OPA decision using the policies and store in runtime props
    result, json_result = tf.check_opa(decision=decision)
    ctx.instance.runtime_properties['opa_evaluation_result'] = result
    ctx.instance.runtime_properties['opa_evaluation_result_json'] = json_result

    # Fail irrecoverably if the policy evaluation failed
    if not result:
        raise NonRecoverableError(
            "Policy evaluation failed. See ""opa_evaluation_result_json"
            "runtime property for full details.")


@operation
@with_terraform
def tfsec(ctx, tf, tfsec_config, **_):
    original_tfsec_config = ctx.instance.runtime_properties.get(
        'tfsec_config') or ctx.node.properties.get('tfsec_config')
    new_config_tfsec = update_dict_values(
        original_tfsec_config, tfsec_config)
    tf.tfsec = TFSec.from_ctx(ctx, new_config_tfsec)
    tf.check_tfsec()
    ctx.instance.runtime_properties['tfsec_config'] = \
        tf.tfsec.export_config()


@operation
@with_terraform
def infracost(ctx, tf, infracost_config, **_):
    original_infracost_config = ctx.instance.runtime_properties.get(
        'infracost_config') or ctx.node.properties.get('infracost_config')
    new_config_infracost = update_dict_values(
        original_infracost_config, infracost_config)
    tf.infracost = Infracost.from_ctx(ctx, new_config_infracost,
                                      tf.variables,
                                      tf.env,
                                      tf.tfvars)
    result, json_result = tf.run_infracost()
    ctx.instance.runtime_properties['infracost'] = json_result
    ctx.instance.runtime_properties['plain_text_infracost'] = result
    ctx.instance.runtime_properties['infracost_config'] = \
        tf.infracost.export_config()


@operation
@with_terraform
def setup_linters(tf, ctx, **_):
    if tf.tflint:
        tf.tflint.validate()
        ctx.instance.runtime_properties['tflint_config'] = \
            tf.tflint.export_config()
    if tf.tfsec:
        tf.tfsec.validate()
        ctx.instance.runtime_properties['tfsec_config'] = \
            tf.tfsec.export_config()
    if tf.terratag:
        tf.terratag.validate()
        ctx.instance.runtime_properties['terratag_config'] = \
            tf.terratag.export_config()


@operation
@with_terraform
def apply(ctx, tf, force=False, **kwargs):
    """
    Execute `terraform apply`.
    """
    if ctx.workflow_id == 'update':
        resource_config = utils.get_resource_config(force=True)
        source = resource_config.get('source')
        source_path = resource_config.get('source_path')
        _reload_template(ctx,
                         tf,
                         source,
                         source_path,
                         **kwargs)
    else:
        old_plan = ctx.instance.runtime_properties.get('plan')
        _apply(tf, old_plan, force)


@operation
@with_terraform
def migrate_state(ctx, tf, backend, backend_config, **_):
    name = backend.get('name')
    options = backend.get('options')
    credentials = backend.get('credentials', {})
    if credentials:
        ctx.logger.info('Credentials are not used in migrate-state.')
    tf.migrate_state(name, options, backend_config)
    resource_config = utils.get_resource_config()
    resource_config.update({'backend': backend})
    utils.update_resource_config(resource_config)


class FailedPlanValidation(NonRecoverableError):
    pass


def compare_plan_results(new_plan, old_plan):

    left = sorted(old_plan.get('resource_changes', []),
                  key=lambda d: d['address'])
    right = sorted(new_plan.get('resource_changes', []),
                   key=lambda d: d['address'])

    diff = DeepDiff(left, right)
    if diff:
        ctx_from_imports.logger.info(
            'Old plan and new plan diff {}'.format(diff))
        raise FailedPlanValidation(
            'The new plan differs from the old plan. '
            'Please Rerun plan workflow before executing apply worfklow.')


def _apply(tf, old_plan=None, force=False):
    try:
        tf.init()
        if tf.terratag:
            tf.run_terratag()
        if old_plan and not force:
            new_plan = tf.plan_and_show()
            compare_plan_results(new_plan, old_plan)
        if not force:
            tf.check_tflint()
            tf.check_tfsec()
        tf.apply()
        tf_state = tf.show()
        tf_output = tf.output()
    except (FailedPlanValidation, TFToolException):
        raise
    except FileNotFoundError as ex:
        _, _, tb = sys.exc_info()
        raise RecoverableError(
            "Failed applying due to syncthing error",
            causes=[exception_to_error_cause(ex, tb)])
    except Exception as ex:
        _, _, tb = sys.exc_info()
        tf.logger.error(str(exception_to_error_cause(ex, tb)))
        raise NonRecoverableError(
            "Failed applying",
            causes=[exception_to_error_cause(ex, tb)])
    utils.refresh_resources_properties(tf_state, tf_output)


def _plan(tf):
    try:
        tf.init()
        if tf.terratag:
            tf.run_terratag()
        tf.state_pull()
        return tf.plan_and_show_two_formats()
    except Exception as ex:
        _, _, tb = sys.exc_info()
        raise NonRecoverableError(
            "Failed executing terraform plan. "
            "If you ran plan prior to installation, verify that the "
            "cloudify.nodes.terraform.Module node template is not dependent "
            "on any uninstalled nodes. Plan is intended for use with "
            "deployment update.",
            causes=[exception_to_error_cause(ex, tb)])


def _handle_new_vars(runtime_props=None,
                     tf=None,
                     variables=None,
                     environment_variables=None,
                     update=False,
                     **_):
    if update:
        resource_config = utils.get_resource_config()
        if variables:
            for k, v in variables.items():
                resource_config['variables'][k] = v
        if environment_variables:
            for k, v in environment_variables.items():
                resource_config['environment_variables'][k] = v
        utils.update_resource_config(resource_config)


@operation
@with_terraform
def plan(ctx,
         tf,
         source=None,
         source_path=None,
         variables=None,
         environment_variables=None,
         **_):
    """
    Execute `terraform plan`.
    """
    _handle_new_vars(ctx.instance.runtime_properties,
                     tf,
                     variables,
                     environment_variables)

    resource_config = utils.get_resource_config()

    if source or source_path:
        source = source or resource_config.get('source')
        source_path = source_path or resource_config.get('source_path')
        tf.root_module = utils.update_terraform_source(source, source_path)
        resource_config.update(
            {
                'source': source,
                'source_path': source_path
            }
        )
    json_result, plain_text_result = _plan(tf)
    ctx.instance.runtime_properties['plan'] = json_result
    ctx.instance.runtime_properties['plain_text_plan'] = plain_text_result
    utils.update_resource_config(resource_config)
    ctx.instance.runtime_properties['previous_tf_state_file'] = \
        utils.get_terraform_state_file(tf.root_module)


@operation
@with_terraform
def check_status(ctx, tf, **_):
    """
    Execute `terraform state pull`.
    """
    status_problems = tf.plan_and_show_state()
    if status_problems:
        ctx.abort_operation(
            'The cloudify.nodes.terraform.Module node template {} '
            'has status problems with these nodes: {}'.format(
                ctx.instance.id, status_problems))
    else:
        ctx.returns(
            'The cloudify.nodes.terraform.Module node template {} '
            'has no status problems.'.format(ctx.instance.id))


@operation
@with_terraform
def check_drift(ctx, tf, **_):

    if ctx.workflow_id == 'update':
        from_node = utils.get_resource_config(force=True)
        _handle_new_vars(**from_node)
        from_inst = utils.get_resource_config(force=False)
        tf.root_module = utils.update_terraform_source(
            from_inst.get('source'),
            from_inst.get('source_path'))
    _state_pull(tf, update_runtime_props=False)
    if ctx.instance.runtime_properties.get(IS_DRIFTED, False):
        ctx.abort_operation(
            'The cloudify.nodes.terraform.Module node template {} '
            'has drifts.'.format(ctx.instance.id))
    else:
        ctx.logger.error(
            'The cloudify.nodes.terraform.Module node instance {} '
            'has no drifts.'.format(ctx.instance.id))


@operation
@with_terraform
def state_pull(ctx, tf, **_):
    """
    Execute `terraform state pull`.
    """
    if ctx.operation.name == 'cloudify.interfaces.lifecycle.pull':
        raise NonRecoverableError(
            'The operation cloudify.interfaces.lifecycle.pull is not a '
            'valid operation. Please use terraform.pull.')
    _state_pull(tf)


def _state_pull(tf, update_runtime_props=True):
    try:
        tf.refresh()
        tf_state = tf.state_pull()
        plan_json = tf.plan_and_show()
        tf_output = tf.output()
    except Exception as ex:
        _, _, tb = sys.exc_info()
        # TODO: make sure it's recoverable only on not syncing plugins
        raise RecoverableError(
            "Failed pulling state",
            causes=[exception_to_error_cause(ex, tb)])
    utils.refresh_resources_properties(
        tf_state, tf_output, update_runtime_props=update_runtime_props)
    utils.refresh_resources_drifts_properties(plan_json)


@operation
@with_terraform
def destroy(ctx, tf, **_):
    """
    Execute `terraform destroy`.
    """
    _destroy(tf)
    try:
        _state_pull(tf)
    except Exception as e:
        ctx.logger.error('State pull after destroy failed: {}'.format(str(e)))
    for runtime_property in ['terraform_source',
                             'last_source_location',
                             'resource_config']:
        ctx.instance.runtime_properties.pop(runtime_property, None)

    tflint_config = ctx.node.properties.get('tflint_config')
    if tflint_config.get('enable'):
        tf.tflint.uninstall_binary()
    tfsec_config = ctx.node.properties.get('tfsec_config', {})
    if tfsec_config.get('enable'):
        tf.tfsec.uninstall_binary()
    terratag_config = ctx.node.properties.get('terratag_config')
    if terratag_config.get('enable'):
        tf.terratag.uninstall_binary()
    infracost_config = ctx.node.properties.get('infracost_config')
    if infracost_config.get('enable'):
        tf.infracost.uninstall_binary()


def _destroy(tf):
    try:
        tf.plan()
        tf.destroy()
    except Exception as ex:
        _, _, tb = sys.exc_info()
        raise NonRecoverableError(
            "Failed destroying",
            causes=[exception_to_error_cause(ex, tb)])


def _reload_template(ctx,
                     tf,
                     source=None,
                     source_path=None,
                     variables=None,
                     environment_variables=None,
                     destroy_previous=False,
                     force=False,
                     **_):

    _handle_new_vars(ctx.instance.runtime_properties,
                     tf,
                     variables,
                     environment_variables,
                     update=True)
    if not any([source, source_path, variables, environment_variables]):
        raise NonRecoverableError(
            "A new value for one of the following parameters must be provided:"
            " source, source_path, variables, environment_variables.")

    resource_config = utils.get_resource_config()
    if not source:
        source = resource_config.get('source')
    if not source_path:
        source_path = resource_config.get('source_path')

    source = utils.handle_previous_source_format(source)
    if destroy_previous:
        destroy(tf=tf, ctx=ctx)
    tf.root_module = utils.update_terraform_source(source, source_path)
    old_plan = ctx.instance.runtime_properties.get('plan')
    _apply(tf, old_plan, force)
    resource_config.update(
        {
            'source': source,
            'source_path': source_path
        }
    )
    utils.update_resource_config(resource_config)
    _state_pull(tf)
    ctx.instance.runtime_properties['previous_tf_state_file'] = \
        utils.get_terraform_state_file(tf.root_module)


@operation
@with_terraform
def reload_template(ctx,
                    tf,
                    source=None,
                    source_path=None,
                    destroy_previous=False,
                    variables=None,
                    environment_variables=None,
                    force=False,
                    **kwargs):
    """
    Terraform reload plan given new location as input
    """

    _reload_template(ctx,
                     tf,
                     source,
                     source_path,
                     variables,
                     environment_variables,
                     destroy_previous,
                     force,
                     **kwargs)


@operation
@with_terraform
def update(ctx, tf, **kwargs):
    resource_config = utils.get_resource_config(force=True)
    source = resource_config.get('source')
    source_path = resource_config.get('source_path')
    _reload_template(ctx,
                     tf,
                     source,
                     source_path,
                     force=True,
                     **kwargs)


@operation
@skip_if_existing
def install(ctx, installation_source=None, **_):
    installation_dir = get_node_instance_dir()
    executable_path = utils.get_executable_path()
    plugins = utils.get_plugins()
    plugins_dir = utils.get_plugins_dir()
    installation_source = \
        installation_source or utils.get_installation_source()

    if os.path.isfile(executable_path) and ctx.workflow_id == 'install':
        ctx.logger.info(
            'Terraform executable already found at {path}; '
            'skipping installation of executable'.format(
                path=executable_path))
    else:
        ctx.logger.warn('You are requesting to write a new file to {loc}. '
                        'If you do not have sufficient permissions, that '
                        'installation will fail.'.format(
                            loc=executable_path))
        install_binary(
            installation_dir, executable_path, installation_source, 'tf.zip')

    # store the values in the runtime for safe keeping -> validation
    ctx.instance.runtime_properties['executable_path'] = executable_path
    utils.handle_plugins(plugins, plugins_dir, installation_dir)


@operation
@skip_if_existing
def uninstall(ctx, **_):
    terraform_config = utils.get_terraform_config()
    resource_config = utils.get_resource_config()
    exc_path = terraform_config.get('executable_path', '')
    system_exc = resource_config.get('use_existing_resource')

    if os.path.isfile(exc_path):
        if system_exc:
            ctx.logger.info(
                'Not removing Terraform installation at {loc} as'
                'it was provided externally'.format(loc=exc_path))
        else:
            ctx.logger.info('Removing executable: {path}'.format(
                path=exc_path))
            os.remove(exc_path)

    for property_name, property_desc in [
        ('plugins_dir',
         'plugins directory'),
        ('storage_path',
         'storage_directory')]:
        dir_to_delete = terraform_config.get(property_name, None)
        if dir_to_delete:
            utils.remove_dir(dir_to_delete, property_desc)


@operation
def set_directory_config(ctx, **_):
    exc_path = utils.get_executable_path(target=True)
    plugins_dir = utils.get_plugins_dir(target=True)
    storage_path = utils.get_storage_path(target=True)
    deployment_terraform_dir = os.path.join(storage_path,
                                            '.terraform')
    resource_node_instance_dir = get_node_instance_dir(source=True)
    if not os.path.exists(resource_node_instance_dir):
        mkdir_p(resource_node_instance_dir)
    resource_terraform_dir = os.path.join(resource_node_instance_dir,
                                          '.terraform')
    resource_plugins_dir = plugins_dir.replace(
        ctx.target.instance.id, ctx.source.instance.id)
    resource_storage_dir = storage_path.replace(
        ctx.target.instance.id, ctx.source.instance.id)

    if utils.is_using_existing(target=True):
        # We are going to use a TF binary at another location.
        # However, we still need to make sure that this directory exists.
        # Otherwise TF will complain. It does not create it.
        # In our other scenario, a symlink is created.
        mkdir_p(resource_terraform_dir)
    else:
        # We don't want to put all the plugins for all the node instances in a
        # deployment multiple times on the system. So here,
        # we already stored it once on the file system, and now we create
        # symlinks so other deployments can use it.
        # TODO: Possibly put this in "apply" and remove the relationship in
        # the future.

        ctx.logger.debug('Creating link {src} {dst}'.format(
            src=deployment_terraform_dir, dst=resource_terraform_dir))
        try:
            os.symlink(deployment_terraform_dir, resource_terraform_dir)
        except OSError:
            ctx.logger.warn('Unable to link {src} {dst}'.format(
                src=deployment_terraform_dir, dst=resource_terraform_dir))
    ctx.logger.debug("setting executable_path to {path}".format(
        path=exc_path))
    ctx.logger.debug("setting plugins_dir to {dir}".format(
        dir=resource_plugins_dir))
    ctx.logger.debug("setting storage_path to {dir}".format(
        dir=resource_storage_dir))
    ctx.source.instance.runtime_properties['executable_path'] = \
        exc_path
    ctx.source.instance.runtime_properties['plugins_dir'] = \
        resource_plugins_dir
    ctx.source.instance.runtime_properties['storage_path'] = \
        resource_storage_dir


def _import_resource(ctx,
                     tf,
                     resource_id,
                     resource_address,
                     source=None,
                     source_path=None,
                     variables=None,
                     environment_variables=None,
                     **_):

    _handle_new_vars(ctx.instance.runtime_properties,
                     tf,
                     variables,
                     environment_variables,
                     update=True)

    if not all([resource_address, resource_id]):
        raise NonRecoverableError(
            "A new value for the following parameters must be provided:"
            " resource_address, resource_id.")

    resource_config = utils.get_resource_config()
    if not source:
        source = resource_config.get('source')
    if not source_path:
        source_path = resource_config.get('source_path')

    source = utils.handle_previous_source_format(source)
    tf.root_module = utils.update_terraform_source(source, source_path)
    resource_config.update(
        {
            'source': source,
            'source_path': source_path
        }
    )
    utils.update_resource_config(resource_config)
    ctx.instance.runtime_properties['previous_tf_state_file'] = \
        utils.get_terraform_state_file(tf.root_module)
    try:
        tf.init()
        tf.import_resource(resource_address, resource_id)
        _state_pull(tf)
    except Exception as ex:
        _, _, tb = sys.exc_info()
        raise NonRecoverableError(
            "Failed executing terraform import. ",
            causes=[exception_to_error_cause(ex, tb)])


@operation
@with_terraform
def import_resource(ctx,
                    tf,
                    resource_id,
                    resource_address,
                    source=None,
                    source_path=None,
                    variables=None,
                    environment_variables=None,
                    **kwargs):
    """
    Terraform import resource given resource_address and resource_id as inputs
    """

    _import_resource(ctx,
                     tf,
                     resource_id,
                     resource_address,
                     source,
                     source_path,
                     variables,
                     environment_variables,
                     **kwargs)
