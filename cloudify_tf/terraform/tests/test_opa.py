
import os
import json
import shutil
from tempfile import mkdtemp
from unittest.mock import patch

from mock import MagicMock

from cloudify.state import current_ctx
from cloudify.mocks import MockCloudifyContext

from .. import opa

OPA_URL = 'https://github.com/open-policy-agent/opa/releases/download/'\
          'v0.47.4/opa_linux_amd64_static'

ctx = MockCloudifyContext(
        'test',
        deployment_id='deployment',
        tenant={'name': 'tenant_test'},
        properties={},
        runtime_properties={},
    )


def opa_params():
    logger_mock = MagicMock()
    params = {
        'logger': logger_mock,
        'deployment_name': 'foo_deployment',
        'node_instance_name': 'foo_instance',
        'installation_source': OPA_URL,
        'executable_path': None,
        'config': None,
        'flags_override': None,
        'env': {},
        'enable': True,
        'policy_bundles': [{"name": "policy",
                           "path": "resources/policy.tgz"}]
    }
    return params


def deny_result_json():
    return json.dumps({
      "result": [
        {
          "path": "blueprint/tf_module/ec2-instance/plan.json",
          "result": [
            [
              "You have disallowed IP addresses in the ingress CIDR blocks"
              "for your security group"
            ]
          ]
        }
      ]
    })


def allow_result_json():
    return json.dumps({
      "result": [
        {
          "path": "blueprint/tf_module/ec2-instance/plan.json",
          "error": {
            "code": "opa_undefined_error",
            "message": "terraform/allow decision was undefined"
          }
        }
      ]
    })


def tf_plan():
    return """{
  "planned_values": {
    "root_module": {
      "resources": [
        {
          "type": "aws_security_group",
          "values": {
            "ingress": [
              {
                "cidr_blocks": [
                  "1.2.3.4/32"
                ]
              }
            ]
          }
        }
      ]
    }
  }
}
    """


def opa_policy():
    return """package terraform
import future.keywords.in
import future.keywords.contains
import input as tfplan
deny contains msg {
  some task in tfplan["planned_values"]["root_module"]["resources"]
  task["type"] == "aws_security_group"
  task["values"]["ingress"][_]["cidr_blocks"][_] == "1.2.3.4/32"
  msg := "Disallowed IP in ingress list"
}
    """


def test_opa_property_name():
    opa_obj = opa.Opa(**opa_params())
    assert opa_obj.config_property_name == 'opa_config'


def test_installation_source():
    opa_obj = opa.Opa(**opa_params())
    assert opa_obj.installation_source == OPA_URL


@patch('cloudify_common_sdk.utils.get_deployment_dir')
def test_executable_path(get_deployment_dir_sdk):
    current_ctx.set(ctx)
    deployment_dir = mkdtemp()
    get_deployment_dir_sdk.return_value = deployment_dir
    expected_path = os.path.join(deployment_dir,
                                 opa_params()['node_instance_name'],
                                 'opa')
    os.makedirs(os.path.dirname(expected_path))
    try:
        opa_obj = opa.Opa(**opa_params())
        actual_path = opa_obj.executable_path
        assert expected_path == actual_path
        assert os.path.isfile(actual_path)
        assert os.path.exists(actual_path)
    finally:
        shutil.rmtree(deployment_dir)


def test_parse_result():
    current_ctx.set(ctx)
    opa_obj = opa.Opa(**opa_params())

    # Test a result that should pass evaluation
    json_input = allow_result_json()
    expected_decision = True
    expected_decision_json = json.loads(allow_result_json())
    actual_decision, actual_decision_json = opa_obj.parse_result(json_input)
    assert expected_decision == actual_decision
    assert expected_decision_json == actual_decision_json

    # Test a result that should fail evaluation
    json_input = deny_result_json()
    expected_decision = False
    expected_decision_json = json.loads(deny_result_json())
    actual_decision, actual_decision_json = opa_obj.parse_result(json_input)
    assert expected_decision == actual_decision
    assert expected_decision_json == actual_decision_json


@patch('cloudify_common_sdk.utils.get_deployment_dir')
def test_evaluation_policy(get_deployment_dir_sdk):
    current_ctx.set(ctx)
    deployment_dir = mkdtemp()
    get_deployment_dir_sdk.return_value = deployment_dir

    # Setup OPA policy directory, which includes "test/policy" because OPA
    # uses the node instance's directory
    opa_policy_dir = os.path.join(deployment_dir, 'test')
    os.mkdir(opa_policy_dir)
    opa_policy_dir = os.path.join(opa_policy_dir, 'policy')
    os.mkdir(opa_policy_dir)

    expected_path = os.path.join(
      deployment_dir, opa_params()['node_instance_name'], 'opa')
    os.makedirs(os.path.dirname(expected_path))

    try:
        # Set up a mock JSON Terraform plan and OPA policy
        opa_file_path = os.path.join(opa_policy_dir, 'main.rego')
        plan_file_path = os.path.join(deployment_dir, 'plan.json')
        with open(plan_file_path, 'w') as planfile:
            with open(opa_file_path, 'w') as opafile:
                opafile.write(opa_policy())
                opafile.flush()
                planfile.write(tf_plan())
                planfile.flush()
                opa_obj = opa.Opa(**opa_params())

                # Evaluate the policy and ensure it fails
                result, result_json = opa_obj.evaluate_policy(
                  plan_file_path, decision="terraform/deny")
                assert result is False
                expected_message = 'Disallowed IP in ingress list'
                actual_message = result_json['result'][0]['result'][0]
                assert expected_message == actual_message
    finally:
        shutil.rmtree(deployment_dir)
