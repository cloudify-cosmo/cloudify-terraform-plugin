
from unittest import TestCase
from mock import patch, MagicMock

try:
    from cloudify.mocks import MockContext
    from cloudify.state import current_ctx
    from cloudify.exceptions import NonRecoverableError
except ImportError:
    from cloudify.mocks import MockContext
    from cloudify.state import current_ctx
    from cloudify.exceptions import NonRecoverableError


from cloudify_tf import workflows
from cloudify_tf import (MODULE_TYPE, FATAL_ERR, CREATE, DELETE, PRECONFIGURE)


class MockedWorkflowCtx(MockContext):

    def graph_mode(self):
        return MagicMock()


class TFWorkflowTests(TestCase):

    @staticmethod
    def _mock_node_context():
        context = MagicMock()
        context.id = 'foo'
        return context

    @staticmethod
    def _mock_instance_context():
        context = MagicMock()
        relationship = MagicMock()
        target_node_instance = MagicMock()
        target_node_instance.state = 'uninitialized'
        relationship.target_node_instance = target_node_instance
        relationship.relationship = MagicMock(
            _relationship={
                workflows.HIERARCHY: [
                    'cloudify.nodes.Root',
                    workflows.REL1,
                    workflows.REL2
                ]
            }
        )
        context.relationships = [relationship]
        return context

    @staticmethod
    def _mock_sequence():
        sequence = MagicMock()
        sequence.add.return_value = None
        return sequence

    def test_plan_module_instance(self):
        ctx = MockContext()
        current_ctx.set(ctx)
        node = self._mock_node_context()
        instance = self._mock_instance_context()
        sequence = self._mock_sequence()
        workflows._plan_module_instance(ctx, node, instance, sequence, {})
        assert sequence.add.call_count == 4

    def test_migrate_state(self):
        ctx = MockedWorkflowCtx()
        current_ctx.set(ctx)
        backend = {}
        backend_config = {'baz': 'qux'}
        nodes = ['foo']
        node_instances = []
        with self.assertRaisesRegex(
                NonRecoverableError, 'No new backend was provided'):
            workflows.migrate_state(
                ctx,
                node_ids=nodes,
                node_instance_ids=node_instances,
                backend=backend,
                backend_config=backend_config
            )
        backend = {'foo': 'bar'}
        nodes = ['foo']
        node_instances = ['bar']
        with self.assertRaisesRegex(
                NonRecoverableError, 'mutually exclusive'):
            workflows.migrate_state(
                ctx,
                node_ids=nodes,
                node_instance_ids=node_instances,
                backend=backend,
                backend_config=backend_config
            )
        with patch('cloudify_tf.workflows._terraform_operation') as op:
            backend = {'foo': 'bar'}
            nodes = ['foo']
            node_instances = []
            kwargs = {
                'backend': backend,
                'backend_config': backend_config
            }
            workflows.migrate_state(
                ctx,
                node_ids=nodes,
                node_instance_ids=node_instances,
                backend=backend,
                backend_config=backend_config
            )
            op.assert_called_once_with(
                ctx,
                "terraform.migrate_state",
                nodes,
                node_instances,
                **kwargs
            )

    def test_terraform_operation(self):
        # Create mock objects
        mock_ctx = MagicMock()
        mock_graph = MagicMock()
        mock_sequence = MagicMock()
        mock_node_instance = MagicMock()
        mock_node_instance.node.type_hierarchy = [MODULE_TYPE]
        mock_node_instance.id = 'node_instance_1'
        mock_node_instance.node.id = 'node_1'

        # Mock methods
        mock_ctx.graph_mode.return_value = mock_graph
        mock_graph.sequence.return_value = mock_sequence
        mock_ctx.node_instances = [mock_node_instance]
        mock_node_instance.execute_operation.return_value = 'operation_result'

        # Define the parameters for the function
        operation = 'test_operation'
        node_ids = ['node_1']
        node_instance_ids = ['node_instance_1']
        kwargs = {'force': 'True'}

        # Call the function
        result_graph = workflows._terraform_operation(
            mock_ctx, operation, node_ids, node_instance_ids, **kwargs)

        # Verify the result
        mock_ctx.graph_mode.assert_called_once()
        mock_graph.sequence.assert_called_once()
        mock_sequence.add.assert_called_once_with(
            mock_node_instance.execute_operation(
                operation, kwargs=kwargs, allow_kwargs_override=True))
        self.assertEqual(result_graph, mock_graph)

    def test_node_ids_continue(self):
        mock_ctx = MagicMock()
        mock_graph = MagicMock()
        mock_sequence = MagicMock()
        mock_node_instance = MagicMock()
        mock_node_instance.node.type_hierarchy = [MODULE_TYPE]
        mock_node_instance.id = 'node_instance_3'
        mock_node_instance.node.id = 'node_3'

        mock_ctx.graph_mode.return_value = mock_graph
        mock_graph.sequence.return_value = mock_sequence
        mock_ctx.node_instances = [mock_node_instance]
        mock_node_instance.execute_operation.return_value = 'operation_result'

        operation = 'test_operation'
        node_ids = ['node_1', 'node_2']  # node_id doesn't match
        node_instance_ids = []  # not filtering by instance ids

        workflows._terraform_operation(
            mock_ctx, operation, node_ids, node_instance_ids)

        mock_ctx.graph_mode.assert_called_once()
        mock_sequence.add.assert_not_called()

    @patch('cloudify_tf.workflows._terraform_operation')
    def test_reload_resources(self, mock_terraform_operation):
        mock_ctx = MagicMock()
        mock_tf_operation_instance = MagicMock()
        mock_terraform_operation.return_value = mock_tf_operation_instance

        node_ids = ['node_1']
        node_instance_ids = ['node_instance_1']
        source = 'http://example.com/source'
        source_path = '/path/to/source'
        variables = {'var1': 'value1'}
        environment_variables = {'env1': 'value1'}
        destroy_previous = True
        force = True

        workflows.reload_resources(
            mock_ctx,
            node_ids,
            node_instance_ids,
            source,
            source_path,
            variables,
            environment_variables,
            destroy_previous,
            force
        )

        mock_terraform_operation.assert_called_once_with(
            mock_ctx,
            "terraform.reload",
            node_ids,
            node_instance_ids,
            source=source,
            source_path=source_path,
            variables=variables,
            environment_variables=environment_variables,
            destroy_previous=destroy_previous,
            force=force
        )

        mock_tf_operation_instance.execute.assert_called_once()

    @patch('cloudify_tf.workflows._terraform_operation')
    def test_import_resource(self, mock_terraform_operation):
        mock_ctx = MagicMock()
        mock_tf_operation_instance = MagicMock()

        mock_terraform_operation.return_value = mock_tf_operation_instance

        node_ids = ['node_1']
        node_instance_ids = ['node_instance_1']
        source = 'http://example.com/source'
        source_path = '/path/to/source'
        variables = {'var1': 'value1'}
        environment_variables = {'env1': 'value1'}
        resource_address = 'address_1'
        resource_id = 'resource_1'

        workflows.import_resource(
            mock_ctx,
            node_ids,
            node_instance_ids,
            source,
            source_path,
            variables,
            environment_variables,
            resource_address,
            resource_id
        )

        mock_terraform_operation.assert_called_once_with(
            mock_ctx,
            "terraform.import_resource",
            node_ids,
            node_instance_ids,
            source=source,
            source_path=source_path,
            variables=variables,
            environment_variables=environment_variables,
            resource_address=resource_address,
            resource_id=resource_id
        )

        mock_tf_operation_instance.execute.assert_called_once()

    @patch('cloudify_tf.workflows._terraform_operation')
    def test_run_infracost(self, mock_terraform_operation):
        mock_ctx = MagicMock()
        mock_tf_operation_instance = MagicMock()

        mock_terraform_operation.return_value = mock_tf_operation_instance

        node_ids = ['node_1']
        node_instance_ids = ['node_instance_1']
        source = 'http://example.com/source'
        source_path = '/path/to/source'
        variables = {'var1': 'value1'}
        environment_variables = {'env1': 'value1'}
        infracost_config = {'config_key': 'config_value'}

        workflows.run_infracost(
            mock_ctx,
            node_ids,
            node_instance_ids,
            source,
            source_path,
            variables,
            environment_variables,
            infracost_config
        )

        mock_terraform_operation.assert_called_once_with(
            mock_ctx,
            "terraform.infracost",
            node_ids,
            node_instance_ids,
            source=source,
            source_path=source_path,
            variables=variables,
            environment_variables=environment_variables,
            infracost_config=infracost_config
        )

        mock_tf_operation_instance.execute.assert_called_once()

    @patch('cloudify_tf.workflows._plan_module_instance')
    @patch('cloudify_tf.workflows.FATAL_ERR')
    def test_terraform_plan_with_node_ids(self,
                                          mock_fatal_err,
                                          mock_plan_module_instance):
        mock_ctx = MagicMock()
        mock_graph = MagicMock()
        mock_sequence = MagicMock()
        mock_node = MagicMock()
        mock_instance = MagicMock()

        mock_node.id = 'node_1'
        mock_node.type_hierarchy = [MODULE_TYPE]
        mock_node.instances = [mock_instance]
        mock_instance.id = 'instance_1'

        mock_ctx.graph_mode.return_value = mock_graph
        mock_graph.sequence.return_value = mock_sequence
        mock_ctx.nodes = [mock_node]
        mock_ctx.node_instances = []

        node_ids = ['node_1']
        node_instance_ids = None
        kwargs = {'key': 'value'}

        workflows.terraform_plan(
            mock_ctx,
            node_ids=node_ids,
            node_instance_ids=node_instance_ids,
            **kwargs
        )

        mock_plan_module_instance.assert_called_once_with(
            mock_ctx,
            mock_node,
            mock_instance,
            mock_sequence,
            kwargs
        )
        mock_graph.execute.assert_called_once()

    @patch('cloudify_tf.workflows._plan_module_instance')
    def test_terraform_plan_mutually_exclusive_params(self,
                                                      _):
        mock_ctx = MagicMock()
        mock_ctx.graph_mode.return_value = MagicMock()

        node_ids = ['node_1']
        node_instance_ids = ['instance_1']
        kwargs = {'key': 'value'}

        with self.assertRaises(FATAL_ERR):
            workflows.terraform_plan(
                mock_ctx,
                node_ids=node_ids,
                node_instance_ids=node_instance_ids,
                **kwargs
            )

    @patch('cloudify_tf.workflows._update_terraform_binary')
    @patch('cloudify_tf.workflows._set_deployment_directory_rel')
    def test_update_terraform_binary_with_node_ids(
            self,
            mock_set_deployment_directory_rel,
            mock_update_terraform_binary):
        mock_ctx = MagicMock()
        mock_graph = MagicMock()
        mock_sequence = MagicMock()
        mock_node = MagicMock()
        mock_instance = MagicMock()
        mock_relationship = MagicMock()

        mock_node.id = 'node_1'
        mock_node.type_hierarchy = ['cloudify.nodes.terraform',
                                    'cloudify.nodes.terraform.Module']
        mock_node.instances = [mock_instance]
        mock_instance.id = 'instance_1'
        mock_instance.relationships = [mock_relationship]
        mock_relationship.target_id = 'instance_1'
        mock_relationship.relationship.type = \
            'cloudify.interfaces.relationship_lifecycle.preconfigure'

        mock_ctx.graph_mode.return_value = mock_graph
        mock_graph.sequence.return_value = mock_sequence
        mock_ctx.nodes = [mock_node]
        mock_ctx.node_instances = []

        node_ids = ['node_1']
        node_instance_ids = None
        installation_source = 'http://example.com/terraform.zip'
        kwargs = {'key': 'value'}

        workflows.update_terraform_binary(
            mock_ctx,
            node_ids=node_ids,
            node_instance_ids=node_instance_ids,
            installation_source=installation_source,
            **kwargs
        )

        mock_update_terraform_binary.assert_called_once_with(
            mock_instance,
            mock_sequence,
            {**kwargs, 'installation_source': installation_source}
        )
        mock_set_deployment_directory_rel.assert_called_once_with(
            mock_instance,
            mock_sequence,
            {**kwargs, 'installation_source': installation_source}
        )
        mock_graph.execute.assert_called_once()

    @patch('cloudify_tf.workflows.FATAL_ERR')
    def test_update_terraform_binary_missing_installation_source(
            self,
            mock_fatal_err):
        mock_ctx = MagicMock()
        mock_fatal_err.side_effect = Exception(
            'You must provided a new URL to Terraform installation source.')

        node_ids = ['node_1']
        node_instance_ids = None
        installation_source = None
        kwargs = {'key': 'value'}

        with self.assertRaises(Exception) as context:
            workflows.update_terraform_binary(
                mock_ctx,
                node_ids=node_ids,
                node_instance_ids=node_instance_ids,
                installation_source=installation_source,
                **kwargs
            )

        self.assertTrue('You must provided a new URL to Terraform installation'
                        ' source.' in str(context.exception))

    @patch('cloudify_tf.workflows.FATAL_ERR')
    def test_update_terraform_binary_FATAL_ERR(self, mock_fatal_err):
        mock_ctx = MagicMock()
        mock_fatal_err.side_effect = Exception(
            'The parameters node_ids and node_instance_ids are mutually '
            'exclusive. node_ids and node_instance_ids were provided.')

        node_ids = ['node_1']
        node_instance_ids = ['instance_1']
        with self.assertRaises(Exception) as context:
            workflows.update_terraform_binary(
                mock_ctx,
                node_ids=node_ids,
                node_instance_ids=node_instance_ids,
                installation_source='http://example.com/terraform.zip'
            )

        self.assertTrue('The parameters node_ids and node_instance_ids are'
                        ' mutually exclusive.' in str(context.exception))
        self.assertTrue('node_ids and node_instance_ids were provided.' in str(
            context.exception))

    @patch('cloudify_tf.workflows._update_terraform_binary')
    @patch('cloudify_tf.workflows._set_deployment_directory_rel')
    @patch('cloudify_tf.workflows.FATAL_ERR')
    def test_update_terraform_binary_with_node_instance_ids(
            self,
            mock_fatal_err,
            mock_set_deployment_directory_rel,
            mock_update_terraform_binary):
        mock_ctx = MagicMock()
        mock_graph = MagicMock()
        mock_sequence = MagicMock()
        mock_instance = MagicMock()
        mock_instance.id = 'instance_1'
        mock_instance.relationships = []
        mock_instance._node_instance.runtime_properties = {}

        mock_ctx.graph_mode.return_value = mock_graph
        mock_graph.sequence.return_value = mock_sequence
        mock_ctx.node_instances = [mock_instance]
        node_ids = None
        node_instance_ids = ['instance_1']
        installation_source = 'http://example.com/terraform.zip'
        kwargs = {'key': 'value'}

        workflows.update_terraform_binary(
            mock_ctx,
            node_ids=node_ids,
            node_instance_ids=node_instance_ids,
            installation_source=installation_source,
            **kwargs
        )

        mock_update_terraform_binary.assert_called_once_with(
            mock_instance,
            mock_sequence,
            {**kwargs, 'installation_source': installation_source}
        )

        mock_set_deployment_directory_rel.assert_not_called()
        mock_graph.execute.assert_called_once()

    def test_update_terraform_binary(self):
        self.instance = MagicMock()
        self.sequence = MagicMock()
        self.kwargs = {'key': 'value'}
        workflows._update_terraform_binary(
            self.instance, self.sequence, self.kwargs)

        self.instance.execute_operation.assert_any_call(
            DELETE, kwargs=self.kwargs, allow_kwargs_override=True)
        self.instance.execute_operation.assert_any_call(
            CREATE, kwargs=self.kwargs, allow_kwargs_override=True)

        self.assertEqual(self.sequence.add.call_count, 2)

        calls = [call[0][0] for call in self.sequence.add.call_args_list]
        self.assertEqual(len(calls), 2)
        self.assertIn(self.instance.execute_operation(
            DELETE, kwargs=self.kwargs, allow_kwargs_override=True), calls)
        self.assertIn(self.instance.execute_operation(
            CREATE, kwargs=self.kwargs, allow_kwargs_override=True), calls)

    def test_set_deployment_directory_rel(self):
        self.instance = MagicMock()
        self.sequence = MagicMock()
        self.kwargs = {'key': 'value'}
        workflows._set_deployment_directory_rel(
            self.instance, self.sequence, self.kwargs)

        self.instance.execute_operation.assert_called_once_with(
            PRECONFIGURE,
            kwargs=self.kwargs,
            allow_kwargs_override=True
        )

        self.sequence.add.assert_called_once()

        call_args = self.sequence.add.call_args[0][0]
        self.assertEqual(call_args, self.instance.execute_operation(
            PRECONFIGURE,
            kwargs=self.kwargs,
            allow_kwargs_override=True
        ))
