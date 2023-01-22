# Copyright (c) 2021 Cloudify Platform Ltd. All rights reserved
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

from cloudify.state import current_ctx

from .. import utils
from . import TestBase
from ..constants import DRIFTS, IS_DRIFTED


class TestUtils(TestBase):
    def setUp(self):
        super(TestUtils, self).setUp()
        self.resource_name = "example_vpc"
        self.vpc_change = {"actions": ["no-op"],
                           "before": {
                               "arn":
                                   "fake_arn",

                               "cidr_block":
                                   "10.10.0.0/16",
                           },
                           "after": {
                               "arn":
                                   "fake_arn",
                               "cidr_block":
                                   "10.10.0.0/16",
                           },
                           "after_unknown": {}}
        self.fake_plan_json = {"format_version": "0.1",
                               "terraform_version": "0.13.4",
                               "variables": {},
                               "planned_values": {},
                               "resource_changes": [
                                   {"address": "aws_vpc.example_vpc",
                                    "mode": "managed",
                                    "type": "aws_vpc",
                                    "name": self.resource_name,
                                    "provider_name":
                                        "registry.terraform.io/hashicorp/aws",
                                    "change": self.vpc_change}],
                               "prior_state": {},
                               "configuration": {}}

    def test_refresh_resources_drifts_properties_no_drifts(self):
        self.fake_plan_json["resource_changes"] = []
        ctx = self.mock_ctx("test_no_op_drifts", None)
        current_ctx.set(ctx=ctx)
        utils.refresh_resources_drifts_properties(self.fake_plan_json)
        self.assertEqual(ctx.instance.runtime_properties[DRIFTS], {})
        self.assertEqual(ctx.instance.runtime_properties[IS_DRIFTED], False)

    def test_refresh_resources_drifts_properties_no_op_drifts(self):
        ctx = self.mock_ctx("test_no_op_drifts", None)
        current_ctx.set(ctx=ctx)
        utils.refresh_resources_drifts_properties(self.fake_plan_json)
        self.assertEqual(ctx.instance.runtime_properties[DRIFTS], {})
        self.assertEqual(ctx.instance.runtime_properties[IS_DRIFTED], False)

    def test_refresh_resources_drifts_properties_with_drifts(self):
        ctx = self.mock_ctx("test_no_op_drifts", None)
        current_ctx.set(ctx=ctx)
        # Change the operation needed just to check we store the changes
        self.vpc_change["actions"] = ["update"]
        utils.refresh_resources_drifts_properties(self.fake_plan_json)
        self.assertEqual(ctx.instance.runtime_properties[IS_DRIFTED], True)
        self.assertDictEqual(ctx.instance.runtime_properties[DRIFTS],
                             {self.resource_name: self.vpc_change})

    def test_backend_string(self):
        backend = {
            'name': 'foo',
            'options': {
                'bucket': 'bucket_name',
                'key': 'key_name',
                'region': 'us-east-1'
            }
        }
        backend_hcl = """terraform {
    backend "foo" {
       bucket = "bucket_name"
       key = "key_name"
       region = "us-east-1"

    }
}"""
        backend_with_dict = {
            'name': 'foo',
            'options': {
                'hostname': 'bar',
                'organization': 'baz',
                'workspaces': {
                    'name': 'taco'
                },
                'token': '%#(##'
            }
        }
        backed_with_dict_hcl = """terraform {
    backend "foo" {
       hostname = "bar"
       organization = "baz"
       workspaces {
          name = "taco"

       }
       token = "%#(##"

    }
}"""

        backend_string = utils.create_backend_string(
            backend['name'], backend['options'])
        backend_dict = utils.create_backend_string(
            backend_with_dict['name'], backend_with_dict['options'])

        self.assertEquals(backend_hcl, backend_string)
        self.assertEquals(backed_with_dict_hcl, backend_dict)

    def test_required_providers_string(self):
        required_providers = {
            "aws": {
                "version": "test"
            },
            "azure": {
                "version": "test"
            }
        }
        required_providers_hcl = """{
    "terraform": {
        "required_providers": {
            "aws": {
                "version": "test"
            },
            "azure": {
                "version": "test"
            }
        }
    }
}"""

        required_providers_string = \
            utils.create_required_providers_string(required_providers)
        self.assertEquals(required_providers_hcl, required_providers_string)

    def test_provider_string(self):
        provider = [{
            'name': 'aws',
            'options': {
                'version': 'version',
                'access_key': 'access-key',
                'region': 'us-east-1'
            }
        }]
        provider_hcl = """provider "aws" {
   version = "version"
   access_key = "access-key"
   region = "us-east-1"

}

"""
        provider_with_dict = [{
            'name': 'azure',
            'options': {
                'client_id': 'client_id',
                'tenant_id': 'tenant_id',
                'features': {
                    'key_vault': {}
                },
                'environment': 'env'
            }
        }]
        provider_with_dict_hcl = """provider "azure" {
   client_id = "client_id"
   tenant_id = "tenant_id"
   features {
      key_vault {

      }

   }
   environment = "env"

}

"""
        providers_hcl = """provider "aws" {
   version = "version"
   access_key = "access-key"
   region = "us-east-1"

}

provider "azure" {
   client_id = "client_id"
   tenant_id = "tenant_id"
   features {
      key_vault {

      }

   }
   environment = "env"

}

"""

        providers = provider + provider_with_dict
        providers_string = utils.create_provider_string(providers)

        provider_string = utils.create_provider_string(provider)
        provider_dict = utils.create_provider_string(provider_with_dict)

        self.assertEquals(provider_hcl, provider_string)
        self.assertEquals(provider_with_dict_hcl, provider_dict)
        self.assertEquals(providers_hcl, providers_string)
