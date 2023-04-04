########
# Copyright (c) 2014-2019 Cloudify Platform Ltd. All rights reserved
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

from os import environ
from json import loads
from contextlib import contextmanager

import pytest

from boto3 import client
from ecosystem_tests.dorkl.constansts import logger
from ecosystem_tests.dorkl import cleanup_on_failure
from ecosystem_tests.dorkl.exceptions import EcosystemTestException
from ecosystem_tests.dorkl.cloudify_api import cloudify_exec, executions_start

TEST_ID = environ.get('__ECOSYSTEM_TEST_ID', 'virtual-machine')


@contextmanager
def test_cleaner_upper():
    try:
        yield
    except Exception:
        cleanup_on_failure(TEST_ID)
        raise


@pytest.mark.dependency(depends=['test_plan_protection'])
def test_drifts(*_, **__):
    with test_cleaner_upper():
        before_props = cloud_resources_node_instance_runtime_properties()
        change_a_resource(before_props)
        executions_start('refresh_terraform_resources', TEST_ID, 150)
        after_props = cloud_resources_node_instance_runtime_properties()
        drifts = after_props.get('drifts')
        logger.info('Drifts: {drifts}'.format(drifts=drifts))
        if drifts:
            return
        raise Exception('The test_drifts test failed.')


@pytest.mark.dependency()
def test_plan_protection(*_, **__):
    with test_cleaner_upper():
        params = {
            'source': 'https://github.com/cloudify-community/tf-source/archive/refs/heads/main.zip',  # noqa
            'source_path': 'template/modules/public_vm',
        }
        executions_start('terraform_plan', TEST_ID, 300, params)
        logger.info('Wrap plan for public VM. '
                    'Now we will run reload_terraform_template for private VM '
                    'and it should fail.')
        params = {
            'source': 'https://github.com/cloudify-community/tf-source/archive/refs/heads/main.zip',  # noqa
            'source_path': 'template/modules/private_vm',
            'force': False
        }
        try:
            executions_start('reload_terraform_template', TEST_ID, 300, params)
        except EcosystemTestException:
            logger.info('Apply caught our plan mismatch.')
        else:
            raise EcosystemTestException(
                'Apply did not catch the plan mismatch.')
        del params['force']
        executions_start('terraform_plan', TEST_ID, 300, params)
        logger.info('Now rerunning apply with a matching plan.')
        before = cloud_resources_node_instance_runtime_properties()
        logger.info('Before outputs: {before}'.format(
            before=before.get('outputs')))
        logger.info('Now rerunning plan.')
        params['force'] = False
        # TODO: IT appears that an earlier change for heal or update broke this. look at changes to wupdate workflow.
        executions_start('reload_terraform_template', TEST_ID, 300, params)
        after = cloud_resources_node_instance_runtime_properties()
        logger.info('After outputs: {after}'.format(
            after=before.get('outputs')))
        if after['outputs'] == before['outputs']:
            raise Exception('Outputs should not match after reload.')


def nodes():
    return cloudify_exec('cfy nodes list')


def node_instances():
    return cloudify_exec('cfy node-instances list -d {}'.format(TEST_ID))


def node_instance_by_name(name):
    for node_instance in node_instances():
        if node_instance['node_id'] == name:
            return node_instance
    raise Exception('No node instances found.')


def node_instance_runtime_properties(name):
    node_instance = cloudify_exec(
        'cfy node-instance get {name}'.format(name=name))
    return node_instance['runtime_properties']


def cloud_resources_node_instance_runtime_properties():
    node_instance = node_instance_by_name('cloud_resources')
    logger.info('Node instance: {node_instance}'.format(
        node_instance=node_instance))
    if not node_instance:
        raise RuntimeError('No cloud_resources node instances found.')
    runtime_properties = node_instance_runtime_properties(
            node_instance['id'])
    logger.info('Runtime properties: {runtime_properties}'.format(
        runtime_properties=runtime_properties))
    if not runtime_properties:
        raise RuntimeError('No cloud_resources runtime_properties found.')
    return runtime_properties


def change_a_resource(props):
    group = props['resources']['example_security_group']
    sg_id = group['instances'][0]['attributes']['id']
    terraform_vars = props['resource_config']['variables']

    environ['AWS_DEFAULT_REGION'] = terraform_vars['aws_region']
    access = get_secret(terraform_vars['access_key'])
    secret = get_secret(terraform_vars['secret_key'])
    token = get_secret(terraform_vars['token'])
    ec2 = client(
        'ec2',
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        aws_session_token=token)
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpProtocol="tcp",
        CidrIp="0.0.0.0/0",
        FromPort=53,
        ToPort=53
    )


def get_secret(value):
    loaded_value = loads(value)
    secret_name = loaded_value['get_secret']
    value = cloudify_exec('cfy secrets get {}'.format(secret_name), log=False)
    return value.get('value')
