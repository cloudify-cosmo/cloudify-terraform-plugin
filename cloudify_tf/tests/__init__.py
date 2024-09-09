
import unittest
from uuid import uuid1
from mock import MagicMock

try:
    from cloudify.mocks import MockcloudifyContext
except ImportError:
    from cloudify.mocks import MockCloudifyContext as MockcloudifyContext


class MockContextAbortOperation(MockcloudifyContext):

    abort_operation = MagicMock()

    @staticmethod
    def returns(value):
        return value

    @property
    def workflow_id(self):
        return self._workflow_id

    @workflow_id.setter
    def workflow_id(self, value):
        self._workflow_id = value


class TestBase(unittest.TestCase):
    def setUp(self):
        super(TestBase, self).setUp()

    def mock_ctx(self,
                 test_name,
                 test_properties,
                 test_runtime_properties=None,
                 operation_name=None,
                 workflow_id=None):

        operation_ctx = {
            'retry_number': 0,
            'name': 'cloudify.interfaces.lifecycle.configure'
        } if not operation_name else {
            'retry_number': 0, 'name': operation_name
        }
        test_node_id = uuid1()
        ctx = MockContextAbortOperation(
            node_id=test_node_id,
            properties=test_properties,
            runtime_properties=test_properties if not test_runtime_properties
            else test_runtime_properties,
            deployment_id=test_name,
            operation=operation_ctx
        )
        ctx.deployment._context['deployment_resource_tags'] = {}
        ctx.workflow_id = workflow_id or 'install'
        return ctx
