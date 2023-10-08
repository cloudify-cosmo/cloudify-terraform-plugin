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

import time
from os import environ
from json import loads, JSONDecodeError
from contextlib import contextmanager

import pytest

from boto3 import client
from ecosystem_tests.dorkl.constansts import logger

from ecosystem_tests.nerdl.api import (
    with_client,
    get_node_instance,
    wait_for_workflow,
    cleanup_on_failure,
    list_node_instances)


TEST_ID = environ.get('__ECOSYSTEM_TEST_ID', 'virtual-machine')

source = 'https://github.com/cloudify-community/tf-source/archive/refs/heads/main.zip'   # noqa

public_params = {
    'source': source,
    'source_path': 'template/modules/public_vm',
}

private_params = {
    'source': source,
    'source_path': 'template/modules/private_vm',
}

private_params_force = {
    'source': source,
    'source_path': 'template/modules/private_vm',
    'force': False
}


@contextmanager
def test_cleaner_upper():
    try:
        yield
    except Exception:
        cleanup_on_failure(TEST_ID)
        raise


@pytest.mark.dependency()
def test_plan_protection(*_, **__):
    with test_cleaner_upper():
        wait_for_workflow(TEST_ID, 'terraform_plan', 300, public_params)
        logger.info('Wrap plan for public VM. '
                    'Now we will run reload_terraform_template for private VM '
                    'and it should fail.')
        try:
            wait_for_workflow(
                TEST_ID, 'reload_terraform_template', 300, private_params_force)
        except Exception:
            logger.error('Apply caught our plan mismatch.'.upper())
        else:
            raise Exception(
                'Apply did not catch the plan mismatch.')
        wait_for_workflow(TEST_ID, 'terraform_plan', 300, private_params)
        time.sleep(10)
        before = cloud_resources_node_instance_runtime_properties()
        logger.error('Before outputs: {before}'.format(
            before=before.get('outputs')))
        logger.error('Now rerunning reload.')
        wait_for_workflow(
            TEST_ID, 'reload_terraform_template', 300, private_params)
        after = cloud_resources_node_instance_runtime_properties()
        logger.info('After outputs: {after}'.format(
            after=before.get('outputs')))
        if after['outputs'] == before['outputs']:
            raise Exception('Outputs should not match after reload.')


@pytest.mark.dependency(depends=['test_plan_protection'])
def test_drifts(*_, **__):
    with test_cleaner_upper():
        before_props = cloud_resources_node_instance_runtime_properties()
        change_a_resource(before_props)
        wait_for_workflow(TEST_ID, 'refresh_terraform_resources', 150)
        after_props = cloud_resources_node_instance_runtime_properties()
        drifts = after_props.get('drifts')
        logger.error('Drifts: {drifts}'.format(drifts=drifts))
        if drifts:
            return
        raise Exception('The test_drifts test failed.')


def node_instance_by_name(name):
    for node_instance in list_node_instances(TEST_ID):
        if node_instance['node_id'] == name:
            return node_instance
    raise Exception('No node instances found.')


def node_instance_runtime_properties(name):
    node_instance = get_node_instance(name)
    return node_instance['runtime_properties']


def cloud_resources_node_instance_runtime_properties():
    node_instance = node_instance_by_name('cloud_resources')
    logger.info('Node instance: {node_instance}'.format(
        node_instance=node_instance))
    if not node_instance:
        raise RuntimeError('No cloud_resources node instances found.')
    runtime_properties = node_instance_runtime_properties(
            node_instance['id'])
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
    client_kwargs = dict(
        aws_access_key_id=access,
        aws_secret_access_key=secret,
    )
    if 'token' in terraform_vars:
        token = get_secret(terraform_vars['token'])
        client_kwargs.update({'aws_session_token': token})
    ec2 = client('ec2', **client_kwargs)
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpProtocol="tcp",
        CidrIp="0.0.0.0/0",
        FromPort=53,
        ToPort=53
    )


@with_client
def get_secret(value, client):
    try:
        loaded_value = loads(value)
    except JSONDecodeError:
        return value
    secret_name = loaded_value['get_secret']
    value = client.secrets.get(secret_name)
    return value.get('value')
