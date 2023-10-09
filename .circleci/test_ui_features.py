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
from contextlib import contextmanager

from ecosystem_tests.dorkl.constansts import logger
from ecosystem_tests.dorkl import cleanup_on_failure
from ecosystem_tests.nerdl.api import (
    get_node_instance,
    cleanup_on_failure,
    list_node_instances)

TEST_ID = environ.get('__ECOSYSTEM_TEST_ID', 'virtual-machine')


@contextmanager
def test_cleaner_upper():
    try:
        yield
    except Exception:
        cleanup_on_failure(TEST_ID)
        raise


def test_runtime_properties():
    with test_cleaner_upper():
        errors = []
        cloud_resources = node_instance_by_name('cloud_resources')
        logger.info('cloud_resources: {}'.format(cloud_resources))
        resources = cloud_resources['runtime_properties'].get('resources', {})
        foo1 = resources.get('foo1', {})
        foo2 = resources.get('foo2', {})
        if not resources:
            errors.append(
                'Resource property not in cloud_resources runtime properties.')
        if not foo1:
            errors.append(
                'foo1 is not in resources property.')
        if not foo2:
            errors.append(
                'foo1 is not in resources property.')
        if not {'mode', 'type', 'provider', 'name', 'instances'}.issubset(
                foo1.keys()):
            errors.append(
                'One or more of mode, type, name, provider, instances '
                'is not in foo1.')
        if not {'mode', 'type', 'provider', 'name', 'instances'}.issubset(
                foo2.keys()):
            errors.append(
                'One or more of mode, type, name, provider, instances '
                'is not in foo1.')
        if not foo1.get('name') == 'foo1':
            errors.append('foo1 name is not foo1')
        if not foo2.get('name') == 'foo2':
            errors.append('foo2 name is not foo2')
        if not foo1.get('mode') == 'managed':
            errors.append('foo1 mode is not managed')
        if not foo2.get('mode') == 'managed':
            errors.append('foo2 mode is not managed')
        if not foo1.get('type') == 'null_resource':
            errors.append('foo1 type is not null_resource')
        if not foo2.get('type') == 'null_resource':
            errors.append('foo2 type is not null_resource')
        if not isinstance(foo1.get('instances'), list):
            errors.append('foo1 instances is not a list.')
        if not isinstance(foo2.get('instances'), list):
            errors.append('foo2 instances is not a list.')
        if errors:
            error_messages = '\n'.join(errors)
            raise Exception(
                'validate_runtime_properties failed because of {}'.format(
                    error_messages))


def node_instances():
    return list_node_instances(TEST_ID)


def node_instance_by_name(name):
    for node_instance in node_instances():
        if node_instance['node_id'] == name:
            return get_node_instance(node_instance['id'])
    raise Exception('No node instances found.')
