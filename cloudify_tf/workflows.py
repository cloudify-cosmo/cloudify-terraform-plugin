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

from cloudify.exceptions import NonRecoverableError

HIERARCHY = 'type_hierarchy'
TF_TYPE = 'cloudify.nodes.terraform'
NOT_STARTED = ['uninitialized', 'deleted']
CREATE = 'cloudify.interfaces.lifecycle.create'
DELETE = 'cloudify.interfaces.lifecycle.delete'
REL1 = 'cloudify.terraform.relationships.run_on_host'
REL2 = 'cloudify.relationships.terraform.run_on_host'
PRECONFIGURE = 'cloudify.interfaces.relationship_lifecycle.preconfigure'


def _terraform_operation(ctx,
                         operation,
                         node_ids,
                         node_instance_ids,
                         **kwargs):
    graph = ctx.graph_mode()
    sequence = graph.sequence()
    # Iterate over all node instances of type "cloudify.nodes.terraform.Module"
    # and refresh states.
    for node_instance in ctx.node_instances:
        if node_ids and (node_instance.node.id not in node_ids):
            continue
        if node_instance_ids and (node_instance.id not in node_instance_ids):
            continue
        if 'cloudify.nodes.terraform.Module' in \
                node_instance.node.type_hierarchy:
            ctx.logger.info("Adding node instance: %s", node_instance.id)
            sequence.add(
                node_instance.execute_operation(
                    operation,
                    kwargs=kwargs,
                    allow_kwargs_override=True)
            )

    return graph


def refresh_resources(ctx, node_ids, node_instance_ids):
    _terraform_operation(
        ctx,
        "terraform.refresh",
        node_ids,
        node_instance_ids).execute()


def reload_resources(ctx,
                     node_ids,
                     node_instance_ids,
                     source,
                     source_path,
                     variables,
                     environment_variables,
                     destroy_previous,
                     force=False):
    kwargs = dict(destroy_previous=destroy_previous)
    if source:
        kwargs['source'] = source
    if source_path:
        kwargs['source_path'] = source_path
    if variables:
        kwargs['variables'] = variables
    if environment_variables:
        kwargs['environment_variables'] = environment_variables
    if force:
        kwargs['force'] = force
    _terraform_operation(
        ctx,
        "terraform.reload",
        node_ids,
        node_instance_ids,
        **kwargs).execute()


def import_resource(ctx,
                    node_ids,
                    node_instance_ids,
                    source,
                    source_path,
                    variables,
                    environment_variables,
                    resource_address,
                    resource_id):
    kwargs = dict(resource_address=resource_address)
    kwargs['resource_id'] = resource_id
    if source:
        kwargs['source'] = source
    if source_path:
        kwargs['source_path'] = source_path
    if variables:
        kwargs['variables'] = variables
    if environment_variables:
        kwargs['environment_variables'] = environment_variables
    _terraform_operation(
        ctx,
        "terraform.import_resource",
        node_ids,
        node_instance_ids,
        **kwargs).execute()


def run_infracost(ctx,
                  node_ids,
                  node_instance_ids,
                  source,
                  source_path,
                  variables,
                  environment_variables,
                  infracost_config):
    kwargs = {}
    if infracost_config:
        kwargs['infracost_config'] = infracost_config
    if source:
        kwargs['source'] = source
    if source_path:
        kwargs['source_path'] = source_path
    if variables:
        kwargs['variables'] = variables
    if environment_variables:
        kwargs['environment_variables'] = environment_variables
    _terraform_operation(
        ctx,
        "terraform.infracost",
        node_ids,
        node_instance_ids,
        **kwargs).execute()


def migrate_state(ctx, node_ids=None, node_instance_ids=None, **kwargs):
    graph = ctx.graph_mode()

    if not kwargs.get('backend'):
        raise NonRecoverableError('No new backend was provided.')

    if node_ids and node_instance_ids:
        raise NonRecoverableError(
            'The parameters node_ids and node_instance_ids are '
            'mutually exclusive. '
            '{} and {} were provided.'.format(node_ids, node_instance_ids)
        )
    _terraform_operation(
        ctx,
        "terraform.migrate_state",
        node_ids,
        node_instance_ids,
        **kwargs).execute()

    return graph.execute()


def terraform_plan(ctx,
                   node_ids=None,
                   node_instance_ids=None,
                   **kwargs):
    """Execute the terraform plan on nodes or node instances.
    :param ctx: The Cloudify Workflow Context from Workflow.
    :type ctx: CloudifyContext
    :param node_ids: A list of node IDs.
    :type node_ids: list
    :param node_instance_ids: A list of node IDs.
      Mutually exclusive with node_ids.
    :param node_instance_ids: list
    :return graph execution.
    :rtype: NoneType
    """

    graph = ctx.graph_mode()
    sequence = graph.sequence()

    if node_ids and node_instance_ids:
        raise NonRecoverableError(
            'The parameters node_ids and node_instance_ids are '
            'mutually exclusive. '
            '{} and {} were provided.'.format(node_ids, node_instance_ids)
        )
    elif node_ids or (not node_ids and not node_instance_ids):
        for node in ctx.nodes:
            if 'cloudify.nodes.terraform.Module' not in node.type_hierarchy:
                continue
            if not node_ids or node.id in node_ids:
                for instance in node.instances:
                    _plan_module_instance(
                        ctx, node, instance, sequence, kwargs)
    elif node_instance_ids:
        for instance in ctx.node_instances:
            if instance.id in node_instance_ids:
                _plan_module_instance(
                    ctx, instance.node, instance, sequence, kwargs)

    return graph.execute()


def _start_terraform_instance(sequence, r):
    sequence.add(
        r.target_node_instance.execute_operation(CREATE))
    sequence.add(
        r.execute_source_operation(PRECONFIGURE))
    sequence.add(
        r.target_node_instance.set_state('started'))


def _plan_module_instance(ctx, node, instance, sequence, kwargs):
    """ Create a task sequence that will execute a terraform plan on
    a list of nodes.

    :param ctx: CloudifyWorkflowContext
    :type ctx: CloudifyWorkflowContext
    :param node: CloudifyWorkflowNode
    :param instance: CloudifyWorkflowNodeInstance
    :param sequence: TaskSequence
    :param kwargs:
    :return: None
    """

    for rel in instance.relationships:
        if rel.target_node_instance.state in NOT_STARTED:
            if REL1 not in rel.relationship._relationship[HIERARCHY] or \
                    REL2 not in rel.relationship._relationship[HIERARCHY]:
                ctx.logger.error(
                    'The Terraform plan node {} is related to the '
                    'node instance {}, which is not in a started '
                    'state. If the Terraform plan is dependent on any '
                    'inputs from node instance {}, '
                    'the plan will fail.'.format(
                        node.id,
                        rel.target_node_instance.id,
                        rel.target_node_instance.id
                    )
                )
            else:
                _start_terraform_instance(sequence, rel)
    sequence.add(
        instance.execute_operation(
            'terraform.plan',
            kwargs=kwargs,
            allow_kwargs_override=True
        )
    )


def update_terraform_binary(ctx,
                            node_ids=None,
                            node_instance_ids=None,
                            installation_source=None,
                            **kwargs):
    if not installation_source:
        raise NonRecoverableError(
            'You must provided a new URL to Terraform installation source.')
    kwargs['installation_source'] = installation_source
    graph = ctx.graph_mode()
    sequence = graph.sequence()
    instance_ids = []
    if node_ids and node_instance_ids:
        raise NonRecoverableError(
            'The parameters node_ids and node_instance_ids are '
            'mutually exclusive. '
            '{} and {} were provided.'.format(node_ids, node_instance_ids)
        )
    elif node_ids or (not node_ids and not node_instance_ids):
        for node in ctx.nodes:
            if 'cloudify.nodes.terraform' not in node.type_hierarchy:
                continue
            if not node_ids or node.id in node_ids:
                for instance in node.instances:
                    if instance.id not in instance_ids:
                        instance_ids.append(instance.id)
                    _update_terraform_binary(instance, sequence, kwargs)
    elif node_instance_ids:
        for instance in ctx.node_instances:
            if instance.id in node_instance_ids:
                if instance.id not in instance_ids:
                    instance_ids.append(instance.id)
                _update_terraform_binary(instance, sequence, kwargs)
    for node in ctx.nodes:
        if 'cloudify.nodes.terraform.Module' not in node.type_hierarchy:
            continue
        for instance in node.instances:
            for relationship in instance.relationships:
                if relationship.target_id in instance_ids and \
                        relationship.relationship.type == PRECONFIGURE:
                    _set_deployment_directory_rel(
                        instance, sequence, kwargs)
            runtime_properties = instance._node_instance.runtime_properties
            runtime_properties['terraform_version'] = {}
            ctx.update_node_instance(node_instance_id=instance.id,
                                     runtime_properties=runtime_properties,
                                     version=instance._node_instance.version)

    return graph.execute()


def _update_terraform_binary(instance, sequence, kwargs):
    sequence.add(
        instance.execute_operation(
            DELETE,
            kwargs=kwargs,
            allow_kwargs_override=True
        )
    )
    sequence.add(
        instance.execute_operation(
            CREATE,
            kwargs=kwargs,
            allow_kwargs_override=True
        )
    )


def _set_deployment_directory_rel(instance, sequence, kwargs):
    sequence.add(
        instance.execute_operation(
            PRECONFIGURE,
            kwargs=kwargs,
            allow_kwargs_override=True
        )
    )
