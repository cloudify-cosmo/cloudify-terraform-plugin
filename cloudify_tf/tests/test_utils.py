
import os
import json
import base64
import zipfile
import requests
import tempfile

from mock import patch, mock_open, MagicMock, Mock
from cloudify.exceptions import NonRecoverableError
from cloudify.state import current_ctx

from cloudify_tf import utils
from cloudify_tf.tests import TestBase
from cloudify_tf.constants import DRIFTS, IS_DRIFTED
from cloudify_tf.utils import (
    dump_file,
    exclude_file,
    get_source_path,
    exclude_dirs,
    file_storage_breaker,
    _zip_archive,
    _unzip_archive,
    update_source_path,
    get_storage_path,
    clean_strings,
    handle_plugins,
    _create_source_path,
    is_using_existing,
    get_binary_location_from_rel,
    update_terraform_source_material,
    get_opa_bundles,
    first_merge_in_second,
    handle_previous_source_format,
    is_url,
    remove_dir,
    get_terraform_state_file,
    try_to_copy_old_state_file,
    store_binary_material,
    extract_binary_tf_data,
    get_repo_url,
    get_plugins_dir,
    TERRAFORM_STATE_FILE
)
pkg = 'cloudify_tf.utils'


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

    @patch('os.path.isfile')
    def test_exclude_file(self, mock_is_file):
        mock_is_file.return_value = True
        test_dir = 'test_dir'
        test_file = 'excluded_file.txt'
        excluded_files = [test_file]

        result = exclude_file(test_dir, 'excluded_file.txt', excluded_files)
        assert result is True
        mock_is_file.return_value = False
        result = exclude_file(test_dir, 'excluded_file.txt', excluded_files)
        assert result is False
        result = exclude_file(test_dir, 'excluded_file.txt', [''])
        assert result is False

    @patch('os.path.isdir')
    def test_exclude_dirs(self, mock_is_isdir):
        self.test_dir = 'test_dir'
        self.subdirs = ['subdir1', 'subdir2', 'subdir3']
        self.excluded_files = ['test_dir/subdir1',
                               'test_dir/subdir2',
                               'excluded_dir']
        mock_is_isdir.return_value = True
        exclude_dirs(self.test_dir, self.subdirs, self.excluded_files)
        self.assertEqual(self.subdirs, ['subdir3'])

        mock_is_isdir.return_value = True
        self.subdirs = ['subdir1', 'subdir2', 'subdir3']
        exclude_dirs(self.test_dir, self.subdirs, [''])
        self.assertEqual(self.subdirs, ['subdir1', 'subdir2', 'subdir3'])

        mock_is_isdir.return_value = False
        exclude_dirs(self.test_dir, self.subdirs, ['non_existent_dir'])
        self.assertEqual(self.subdirs, ['subdir1', 'subdir2', 'subdir3'])

    @patch(f'{pkg}.Path.stat')
    @patch(f'{pkg}.Path.is_file', return_value=True)
    @patch(f'{pkg}.get_ctx_node')
    def test_file_storage_breaker(self,
                                  mock_get_ctx_node,
                                  mock_isfile,
                                  mock_stat):
        mock_stat.return_value.st_size = 40000
        mock_get_ctx_node.return_value.properties = {
            'max_stored_filesize': 500000}

        result = file_storage_breaker('test_file.txt')
        self.assertFalse(result)

    @patch('os.walk')
    @patch('builtins.open', new_callable=mock_open)
    @patch(f'{pkg}.exclude_dirs')
    @patch(f'{pkg}.exclude_file')
    @patch(f'{pkg}.file_storage_breaker')
    @patch('os.path.isfile')
    @patch('os.path.isdir')
    @patch('os.stat')
    def test_zip_archive(self,
                         mock_stat,
                         mock_isdir,
                         mock_isfile,
                         mock_file_storage_breaker,
                         mock_exclude_file,
                         mock_exclude_dirs,
                         mock_open,
                         mock_os_walk):
        mock_os_walk.return_value = [
            ('extracted_source',
             ['subdir1', 'subdir2'],
             ['file1.txt', 'file2.txt']),
            ('extracted_source/subdir1', [], ['file3.txt']),
            ('extracted_source/subdir2', [], ['file4.txt']),
        ]
        mock_exclude_file.side_effect = \
            lambda dirname, filename, exclude_files: filename == 'file2.txt'
        mock_file_storage_breaker.side_effect = \
            lambda filepath: filepath == 'extracted_source/subdir2/file4.txt'
        mock_isfile.return_value = True
        mock_isdir.return_value = False
        mock_stat.return_value = MagicMock(
            st_size=100,
            st_mode=0o100644,
            st_mtime=1577836800,  # 2020-01-01 00:00:00
            st_ctime=1577836800,
            st_atime=1577836800
        )

        archive_path = _zip_archive(
            'extracted_source', exclude_files=['file2.txt'])

        with zipfile.ZipFile(archive_path, 'r') as zip_ref:
            zip_contents = zip_ref.namelist()

        expected_files = [
            'file1.txt',
            'subdir1/file3.txt'
        ]
        self.assertEqual(set(zip_contents), set(expected_files))

        os.remove(archive_path)

    @patch(f'{pkg}.unzip_archive')
    @patch(f'{pkg}.copy_directory')
    @patch(f'{pkg}.remove_dir')
    def test_unzip_archive(self,
                           mock_remove_dir,
                           mock_copy_directory,
                           mock_unzip_archive):
        mock_unzip_archive.return_value = '/tmp/unzipped_source'
        archive_path = 'test_archive.zip'
        target_directory = 'target_dir'

        result = _unzip_archive(archive_path, target_directory)

        mock_unzip_archive.assert_called_once_with(
            archive_path, skip_parent_directory=False)
        mock_copy_directory.assert_called_once_with(
            '/tmp/unzipped_source', target_directory)
        mock_remove_dir.assert_called_once_with('/tmp/unzipped_source')
        self.assertEqual(result, target_directory)

    def test_refresh_resources_drifts_properties_no_drifts(self):
        self.fake_plan_json["resource_changes"] = []
        ctx = self.mock_ctx("test_no_op_drifts", None)
        current_ctx.set(ctx=ctx)
        utils.refresh_resources_drifts_properties(self.fake_plan_json)
        self.assertEqual(ctx.instance.runtime_properties[DRIFTS], {})
        self.assertEqual(ctx.instance.runtime_properties[IS_DRIFTED], False)

    def test_clean_strings(self):
        self.assertEqual(clean_strings("test'string"), b"test'string")
        self.assertEqual(clean_strings("'test'string'"), b"test'string")
        self.assertEqual(clean_strings("'test string'"), b"test string")
        self.assertEqual(clean_strings("test string"), b"test string")

        self.assertEqual(clean_strings(b"test string"), b"test string")
        self.assertEqual(clean_strings(123), 123)
        self.assertEqual(clean_strings(None), None)
        self.assertEqual(clean_strings(["test string"]), ["test string"])

    @patch(f'{pkg}.remove_dir')
    @patch(f'{pkg}.untar_archive')
    @patch(f'{pkg}.unzip_archive')
    @patch(f'{pkg}.os.path.isfile')
    @patch(f'{pkg}.os.path.isabs')
    def test_create_source_path(self,
                                mock_isabs,
                                mock_isfile,
                                mock_unzip_archive,
                                mock_untar_archive,
                                mock_remove_dir):
        mock_unzip_archive.return_value = 'unzipped_directory'
        mock_untar_archive.return_value = 'untar_directory'
        mock_isabs.return_value = True
        mock_isfile.return_value = True

        # Call the function with a mock path zip
        source_tmp_path, delete_tmp = _create_source_path(
            'temp_dir/some_relative_path.zip')

        self.assertEqual(source_tmp_path, 'unzipped_directory')
        self.assertFalse(delete_tmp)
        mock_unzip_archive.assert_called_once_with(
            'temp_dir/some_relative_path.zip', False)

        # Call the function with a mock path tar
        source_tmp_path, delete_tmp = _create_source_path(
            'temp_dir/some_relative_path.tar')

        self.assertEqual(source_tmp_path, 'untar_directory')
        self.assertFalse(delete_tmp)
        mock_unzip_archive.assert_called_once_with(
            'temp_dir/some_relative_path.zip', False)

    @patch(f'{pkg}.find_terraform_node_from_rel')
    @patch(f'{pkg}.get_resource_config')
    def test_is_using_existing(self,
                               mock_get_resource_config,
                               mock_find_terraform_node_from_rel):
        mock_get_resource_config.return_value = {
            'use_existing_resource': False}

        self.assertFalse(is_using_existing(target=True))
        mock_get_resource_config.assert_called_once_with(target=True)
        mock_find_terraform_node_from_rel.assert_not_called()

    @patch(f'{pkg}.find_terraform_node_from_rel')
    @patch(f'{pkg}.get_resource_config')
    def test_is_using_existing_without_target(self,
                                              mock_get_resource_config,
                                              mock_find_tf_node_from_rel):

        mock_tf_rel = MagicMock()
        mock_tf_rel.target.instance.runtime_properties.get.return_value = {
            'use_existing_resource': False}
        mock_find_tf_node_from_rel.return_value = mock_tf_rel

        self.assertFalse(is_using_existing(target=False))
        mock_get_resource_config.assert_called_once_with(target=False)
        mock_find_tf_node_from_rel.assert_called_once()

    @patch(f'{pkg}.get_executable_path')
    @patch(f'{pkg}.os.path.isfile')
    @patch(f'{pkg}.find_terraform_node_from_rel')
    def test_binary_location_from_rel_candidate_b(self,
                                                  mock_find_tf_node_from_rel,
                                                  mock_isfile,
                                                  mock_get_executable_path):
        mock_get_executable_path.return_value = '/path/to/executable'
        mock_isfile.return_value = True

        result = get_binary_location_from_rel()

        self.assertEqual(result, '/path/to/executable')
        mock_get_executable_path.assert_called_once()
        mock_isfile.assert_called_once_with('/path/to/executable')
        mock_find_tf_node_from_rel.assert_not_called()

    @patch(f'{pkg}.get_executable_path')
    @patch(f'{pkg}.os.path.isfile')
    @patch(f'{pkg}.find_terraform_node_from_rel')
    def test_binary_location_from_rel_candidate_a(self,
                                                  mock_find_tf_node_from_rel,
                                                  mock_isfile,
                                                  mock_get_executable_path):
        mock_get_executable_path.return_value = None
        mock_tf_rel = MagicMock()
        mock_tf_rel.target.node.properties.get.return_value = {
            'executable_path': '/path/to/executable'}
        mock_find_tf_node_from_rel.return_value = mock_tf_rel
        mock_isfile.side_effect = lambda x: x == '/path/to/executable'

        result = get_binary_location_from_rel()

        self.assertEqual(result, '/path/to/executable')
        mock_get_executable_path.assert_called_once()
        mock_find_tf_node_from_rel.assert_called_once()
        mock_isfile.assert_any_call('/path/to/executable')

    @patch(f'{pkg}.get_executable_path')
    @patch(f'{pkg}.os.path.isfile')
    @patch(f'{pkg}.find_terraform_node_from_rel')
    def test_binary_location_from_rel_not_found(self,
                                                mock_find_tf_node_from_rel,
                                                mock_isfile,
                                                mock_get_executable_path):
        mock_get_executable_path.return_value = None
        mock_find_tf_node_from_rel.return_value = None
        mock_isfile.return_value = False

        with self.assertRaises(NonRecoverableError):
            get_binary_location_from_rel()

        mock_get_executable_path.assert_called_once()
        mock_find_tf_node_from_rel.assert_called_once()

    @patch(f'{pkg}.os.remove')
    @patch(f'{pkg}._file_to_base64')
    @patch(f'{pkg}._zip_archive')
    @patch(f'{pkg}.v1_gteq_v2')
    @patch(f'{pkg}.get_ne_version')
    @patch(f'{pkg}.copy_directory')
    @patch(f'{pkg}._create_source_path')
    @patch(f'{pkg}.get_shared_resource')
    @patch(f'{pkg}.get_node_instance_dir')
    @patch(f'{pkg}.ctx')
    def test_update_terraform_source_material_zip(
            self,
            mock_ctx,
            mock_get_node_instance_dir,
            mock_get_shared_resource,
            mock_create_source_path,
            mock_copy_directory,
            mock_get_ne_version,
            mock_v1_gteq_v2,
            mock_zip_archive,
            mock_file_to_base64,
            mock_os_remove):

        # Setup mock values
        mock_get_node_instance_dir.return_value = '/mock/node_instance_dir'
        mock_get_shared_resource.return_value = '/mock/source_tmp_path'
        mock_create_source_path.return_value = ('/mock/source_tmp_path', True)
        mock_get_ne_version.return_value = '5.0.0'
        mock_v1_gteq_v2.return_value = False
        mock_zip_archive.return_value = '/mock/terraform_source.zip'
        mock_file_to_base64.return_value = 'mock_base64_encoded'

        # Call the function
        new_source = 'http://mock_source.zip'
        result = update_terraform_source_material(new_source)

        # Verify expected calls and results
        # mock_create_source_path.assert_called_once_with('/mock/source_tmp_path')
        mock_zip_archive.assert_called_once_with('/mock/source_tmp_path')
        mock_file_to_base64.assert_called_once_with(
            '/mock/terraform_source.zip')
        mock_os_remove.assert_called_once_with('/mock/terraform_source.zip')
        self.assertEqual(result, 'mock_base64_encoded')

    @patch(f'{pkg}.remove_dir')
    @patch(f'{pkg}.copy_directory')
    @patch(f'{pkg}.mkdir_p')
    @patch(f'{pkg}._create_source_path')
    @patch(f'{pkg}.get_shared_resource')
    @patch(f'{pkg}.get_node_instance_dir')
    @patch(f'{pkg}.get_ctx_instance')
    def test_get_opa_bundles(self,
                             mock_get_ctx_instance,
                             mock_get_node_instance_dir,
                             mock_get_shared_resource,
                             mock_create_source_path,
                             mock_mkdir_p,
                             mock_copy_directory,
                             mock_remove_dir):
        ctx = self.mock_ctx("test_get_opa_bundles", None)
        current_ctx.set(ctx=ctx)
        # Setup mock values
        mock_get_ctx_instance.return_value.runtime_properties = {
            'opa_config': {
                'policy_bundles': [
                    {
                        'location': 'http://mock_bundle.zip',
                        'name': 'mock_bundle',
                        'username': 'mock_user',
                        'password': 'mock_pass'
                    }
                ]
            }
        }
        mock_get_node_instance_dir.return_value = '/mock/node_instance_dir'
        mock_get_shared_resource.return_value = '/mock/bundle_tmp_path'
        mock_create_source_path.return_value = ('/mock/bundle_tmp_path', True)

        # Call the function
        get_opa_bundles()

        # Verify expected calls and results
        mock_get_shared_resource.assert_called_once_with(
            'http://mock_bundle.zip', dir='/mock/node_instance_dir',
            username='mock_user', password='mock_pass'
        )
        mock_create_source_path.assert_called_once_with(
            '/mock/bundle_tmp_path')
        mock_remove_dir.assert_called_once_with('/mock/bundle_tmp_path')

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

    def test_first_merge_in_second(self):
        new_dict = {'a': 1, 'b': 2}
        original_dict = {'b': 3, 'c': 4}
        expected_result = {'b': 2, 'c': 4, 'a': 1}
        result = first_merge_in_second(new_dict, original_dict)
        self.assertEqual(result, expected_result)

        new_dict = {}
        original_dict = {'b': 3, 'c': 4}
        expected_result = {'b': 3, 'c': 4}
        result = first_merge_in_second(new_dict, original_dict)
        self.assertEqual(result, expected_result)

        new_dict = {'a': 1}
        original_dict = {}
        expected_result = {'a': 1}
        result = first_merge_in_second(new_dict, original_dict)
        self.assertEqual(result, expected_result)

        new_dict = {}
        original_dict = {}
        expected_result = {}
        result = first_merge_in_second(new_dict, original_dict)
        self.assertEqual(result, expected_result)

        with self.assertRaises(TypeError):
            first_merge_in_second(None, {})
        with self.assertRaises(TypeError):
            first_merge_in_second({}, None)
        with self.assertRaises(TypeError):
            first_merge_in_second(None, None)
        with self.assertRaises(TypeError):
            first_merge_in_second([], {})

    @patch(f'{pkg}.is_url')
    def test_dict_input(self, mock_is_url):
        ctx = self.mock_ctx("test_dict_input", None)
        current_ctx.set(ctx=ctx)
        source = {'key': 'value'}
        result = handle_previous_source_format(source)
        self.assertEqual(result, source)

        source = '/some/path'
        result = handle_previous_source_format(source)
        self.assertEqual(result, {'location': '/some/path'})

        mock_is_url.return_value = True
        source = 'http://example.com'
        result = handle_previous_source_format(source)
        self.assertEqual(result, {'location': 'http://example.com'})

        mock_is_url.return_value = False
        source = json.dumps({'key': 'value'})
        result = handle_previous_source_format(source)
        self.assertEqual(result, {'key': 'value'})

        mock_is_url.return_value = False
        source = 'invalid json'
        result = handle_previous_source_format(source)
        self.assertEqual(result, 'invalid json')

        mock_is_url.return_value = False
        source = 1234
        result = handle_previous_source_format(source)
        self.assertEqual(result, 1234)

    @patch('requests.get')
    def test_valid_url(self, mock_requests_get):
        # Mocking requests.get to return a response with status code 200
        mock_response = Mock()
        mock_response.status_code = 200
        mock_requests_get.return_value = mock_response

        result = is_url('http://example.com')
        self.assertTrue(result)

    @patch('requests.get')
    def test_invalid_url(self, mock_requests_get):
        # Mocking requests.get to raise a ConnectionError
        mock_requests_get.side_effect = requests.ConnectionError

        result = is_url('http://example.com')
        self.assertFalse(result)

    @patch(f'{pkg}.os.listdir')
    @patch(f'{pkg}.os.path.exists')
    def test_get_terraform_state_file_target_dir_with_tfstate(self,
                                                              mock_exists,
                                                              mock_listdir):
        mock_listdir.return_value = ['terraform.tfstate']
        mock_exists.return_value = True

        target_dir = '/fake/dir'
        result = get_terraform_state_file(target_dir=target_dir)
        self.assertEqual(result, os.path.join(target_dir, 'terraform.tfstate'))

    @patch(f'{pkg}.os.listdir')
    @patch(f'{pkg}.os.path.exists')
    @patch(f'{pkg}.get_storage_path')
    @patch(f'{pkg}.get_terraform_source_material')
    @patch(f'{pkg}.get_source_path')
    @patch(f'{pkg}._unzip_archive')
    @patch(f'{pkg}.filecmp.cmp')
    @patch(f'{pkg}.shutil.move')
    @patch(f'{pkg}.shutil.rmtree')
    @patch(f'{pkg}.ctx.logger.warn')
    @patch(f'{pkg}.ctx.logger.debug')
    def test_get_terraform_state_file_no_tfstate_and_unzipping_success(
            self,
            mock_debug,
            mock_warn,
            mock_rmtree,
            mock_move,
            mock_cmp,
            mock_unzip,
            mock_get_source_path,
            mock_get_terraform_source_material,
            mock_get_storage_path,
            mock_exists,
            mock_listdir):

        mock_listdir.return_value = []
        mock_exists.return_value = False
        mock_get_storage_path.return_value = '/fake/storage'
        mock_get_terraform_source_material.return_value = base64.b64encode(
            b'some data').decode('utf-8')
        mock_get_source_path.return_value = '/fake/source'
        mock_unzip.return_value = '/fake/extracted'
        mock_cmp.return_value = True
        mock_move.return_value = None

        with patch(f'{pkg}.tempfile.NamedTemporaryFile', mock_open()):
            result = get_terraform_state_file(target_dir='/fake/dir')
            self.assertEqual(
                result, '/fake/storage/{}'.format(TERRAFORM_STATE_FILE))
            mock_debug.assert_called_once_with(
                'TF State file: /fake/storage/{}.'.format(
                    TERRAFORM_STATE_FILE))

    @patch(f'{pkg}.os.listdir')
    @patch(f'{pkg}.os.path.exists')
    @patch(f'{pkg}.get_storage_path')
    @patch(f'{pkg}.get_terraform_source_material')
    @patch(f'{pkg}.get_source_path')
    @patch(f'{pkg}._unzip_archive')
    @patch(f'{pkg}.filecmp.cmp')
    @patch(f'{pkg}.shutil.rmtree')
    @patch(f'{pkg}.ctx.logger.warn')
    @patch(f'{pkg}.ctx.logger.debug')
    def test_get_terraform_state_file_unzipping_fail(
            self,
            mock_debug,
            mock_warn,
            mock_rmtree,
            mock_cmp,
            mock_unzip,
            mock_get_source_path,
            mock_get_terraform_source_material,
            mock_get_storage_path,
            mock_exists,
            mock_listdir):

        mock_listdir.return_value = []
        mock_exists.return_value = False
        mock_get_storage_path.return_value = '/fake/storage'
        mock_get_terraform_source_material.return_value = base64.b64encode(
            b'some data').decode('utf-8')
        mock_get_source_path.return_value = '/fake/source'
        mock_unzip.side_effect = zipfile.BadZipFile

        with patch(f'{pkg}.tempfile.NamedTemporaryFile', mock_open()):
            result = get_terraform_state_file(target_dir='/fake/dir')
            self.assertIsNone(result)
            mock_debug.assert_not_called()

    @patch(f'{pkg}.os.path.exists')
    @patch(f'{pkg}.os.symlink')
    @patch(f'{pkg}.os.stat')
    @patch(f'{pkg}.ctx')
    def test_try_to_copy_old_state_file_success(self,
                                                mock_ctx,
                                                mock_stat,
                                                mock_symlink,
                                                mock_exists):
        mock_ctx.instance.runtime_properties = {
            'previous_tf_state_file': '/path/to/old_state_file'}
        mock_exists.return_value = True
        mock_stat.return_value.st_size = 100  # Simulate a non-empty file

        try_to_copy_old_state_file('/target/dir')

        target_file = os.path.join('/target/dir', 'old_state_file')
        mock_symlink.assert_called_once_with(
            '/path/to/old_state_file', target_file)
        self.assertEqual(
            mock_ctx.instance.runtime_properties['previous_tf_state_file'],
            '/path/to/old_state_file')

    @patch(f'{pkg}.os.path.exists')
    @patch(f'{pkg}.os.symlink')
    @patch(f'{pkg}.os.stat')
    @patch(f'{pkg}.ctx')
    def test_try_to_copy_old_state_file_no_file(self,
                                                mock_ctx,
                                                mock_stat,
                                                mock_symlink,
                                                mock_exists):
        mock_ctx.instance.runtime_properties = {
            'previous_tf_state_file': '/path/to/old_state_file'}
        mock_exists.return_value = False

        try_to_copy_old_state_file('/target/dir')

        mock_symlink.assert_not_called()
        self.assertEqual(
            mock_ctx.instance.runtime_properties['previous_tf_state_file'],
            '/path/to/old_state_file')

    @patch(f'{pkg}.os.path.exists')
    @patch(f'{pkg}.os.symlink')
    @patch(f'{pkg}.os.stat')
    @patch(f'{pkg}.ctx')
    def test_try_to_copy_old_state_file_symlink_error(self,
                                                      mock_ctx,
                                                      mock_stat,
                                                      mock_symlink,
                                                      mock_exists):
        mock_ctx.instance.runtime_properties = {
            'previous_tf_state_file': '/path/to/old_state_file'}
        mock_exists.return_value = True
        mock_stat.return_value.st_size = 100  # Simulate a non-empty file
        mock_symlink.side_effect = OSError('Symlink error')

        with patch(f'{pkg}.ctx.logger.warn') as mock_warn:
            try_to_copy_old_state_file('/target/dir')
            target_file = os.path.join('/target/dir', 'old_state_file')
            mock_symlink.assert_called_once_with('/path/to/old_state_file',
                                                 target_file)
            mock_warn.assert_called_once_with(
                'Unable to link {src} {dst}'.format(
                    src='/path/to/old_state_file', dst=target_file))
            self.assertEqual(
                mock_ctx.instance.runtime_properties['previous_tf_state_file'],
                '/path/to/old_state_file')

    @patch(f'{pkg}.ctx')
    @patch(f'{pkg}._zip_archive')
    @patch(f'{pkg}._file_to_base64')
    @patch(f'{pkg}.get_executable_path')
    @patch(f'{pkg}.get_plugins_dir')
    @patch(f'{pkg}.get_ctx_node')
    def test_store_binary_material_large_base64(self,
                                                mock_get_ctx_node,
                                                mock_get_plugins_dir,
                                                mock_get_executable_path,
                                                mock_file_to_base64,
                                                mock_zip_archive,
                                                mock_ctx):
        # Setup mocks
        mock_zip_archive.return_value = 'archive.zip'
        mock_file_to_base64.return_value = 'a' * 200000  # Large base64 string
        mock_get_executable_path.return_value = 'executable_path'
        mock_get_plugins_dir.return_value = 'plugins_dir'
        mock_get_ctx_node.return_value.properties = {
            'max_runtime_property_size': 100000}

        # Mock os.remove to avoid FileNotFoundError
        with patch(f'{pkg}.os.remove'):
            with self.assertRaises(Exception):
                store_binary_material('/path/to/module_root')

    @patch(f'{pkg}._unzip_archive')
    @patch(f'{pkg}.base64.decode')
    def test_extract_binary_tf_data(self,
                                    mock_base64_decode,
                                    mock_unzip_archive):
        mock_unzip_archive.return_value = None
        mock_base64_decode.return_value = None

        # Create temporary directories
        with tempfile.TemporaryDirectory() as root_dir:
            with tempfile.TemporaryDirectory() as source_path:
                mock_data = 'dGVzdC1kYXRh'
                extract_binary_tf_data(root_dir, mock_data, source_path)
                mock_base64_decode.assert_called_once()

    def test_dump_file_real(self):
        with tempfile.TemporaryDirectory() as work_directory:
            file_name = 'test_file.txt'
            output = 'This is some test output.'

            dump_file(output, work_directory, file_name)

            file_path = os.path.join(work_directory, file_name)

            self.assertTrue(os.path.isfile(file_path))

            with open(file_path, 'r') as f:
                content = f.read()
            self.assertEqual(content, output)

    @patch(f'{pkg}.create_plugins_dir')
    @patch(f'{pkg}.download_file')
    @patch(f'{pkg}.unzip_and_set_permissions')
    @patch(f'{pkg}.mkdir_p')
    def test_handle_plugins_success(self,
                                    mock_mkdir_p,
                                    mock_unzip_and_set_permissions,
                                    mock_download_file,
                                    mock_create_plugins_dir):
        # Setup mocks
        mock_create_plugins_dir.return_value = None
        mock_download_file.return_value = None
        mock_unzip_and_set_permissions.return_value = None
        mock_mkdir_p.return_value = None

        plugins = {
            'registry.terraform.io/hashicorp/template':
            'https://example.com/terraform-provider-template.zip'
        }

        with tempfile.TemporaryDirectory() as plugins_dir:
            with tempfile.TemporaryDirectory() as installation_dir:
                handle_plugins(plugins, plugins_dir, installation_dir)

                # Assertions
                mock_create_plugins_dir.assert_called_once_with(plugins_dir)
                mock_download_file.assert_called_once()
                mock_unzip_and_set_permissions.assert_called_once()
                mock_mkdir_p.assert_called_once()

                self.assertFalse(
                    os.path.exists(mock_download_file.call_args[0][0]))

    @patch(f'{pkg}.create_plugins_dir')
    @patch(f'{pkg}.download_file')
    @patch(f'{pkg}.unzip_and_set_permissions')
    @patch(f'{pkg}.mkdir_p')
    def test_handle_plugins_invalid_input(self,
                                          mock_mkdir_p,
                                          mock_unzip_and_set_permissions,
                                          mock_download_file,
                                          mock_create_plugins_dir):
        with self.assertRaises(NonRecoverableError):
            handle_plugins(
                'invalid', '/some/plugins_dir', '/some/installation_dir')

    @patch(f'{pkg}.ctx')
    def test_remove_symlink(self, mock_ctx):
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            symlink_path = temp_file.name + '_symlink'
            os.symlink(temp_file.name, symlink_path)

        mock_ctx.logger.debug = MagicMock()

        remove_dir(symlink_path, desc='symlink')

        self.assertFalse(os.path.exists(symlink_path))
        mock_ctx.logger.debug.assert_called_once_with(
            f'Unlinking: {symlink_path}')

    @patch(f'{pkg}.ctx')
    def test_remove_file(self, mock_ctx):
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            file_path = temp_file.name

        mock_ctx.logger.debug = MagicMock()

        remove_dir(file_path, desc='file')

        self.assertFalse(os.path.exists(file_path))
        mock_ctx.logger.debug.assert_called_once_with(
            f'Removing file {file_path}')

    @patch(f'{pkg}.ctx')
    @patch('shutil.rmtree')
    def test_remove_directory_failure(self, mock_rmtree, mock_ctx):
        # Setup
        with tempfile.TemporaryDirectory() as temp_dir:
            sub_dir = os.path.join(temp_dir, 'sub_dir')
            os.makedirs(sub_dir)

        # Make shutil.rmtree raise an OSError
        mock_rmtree.side_effect = OSError('Permission denied')
        mock_ctx.logger.debug = MagicMock()
        mock_ctx.logger.error = MagicMock()

        # Test
        remove_dir(temp_dir, desc='directory')

        # Assertions
        self.assertTrue(os.path.exists(temp_dir))  # Should still exist
        mock_ctx.logger.debug.assert_called_once_with(
            f'Removing directory: {temp_dir}')
        mock_ctx.logger.error.assert_called_once_with(
            'Unable to safely remove temporary extraction of archive. '
            'Error: Permission denied'
        )

    @patch(f'{pkg}.get_resource_config')
    def test_update_source_path_with_valid_path(self,
                                                mock_get_resource_config):
        mock_get_resource_config.return_value = {
            'source': {'existing_key': 'existing_value'}
        }

        source_path = '/new/source/path'
        updated_config = update_source_path(source_path)

        expected_config = {
            'source': {
                'existing_key': 'existing_value',
                'source_path': source_path
            },
            'source_path': source_path
        }

        self.assertEqual(updated_config, expected_config)

    @patch(f'{pkg}.get_resource_config')
    def test_get_source_path_with_source_in_resource_config(
            self,
            mock_get_resource_config):
        mock_get_resource_config.return_value = {
            'source': {'source_path': '/source/path/from/source'}
        }
        source_path = get_source_path()
        self.assertEqual(source_path, '/source/path/from/source')

    @patch(f'{pkg}.get_resource_config')
    def test_get_source_path_with_no_source_path(self,
                                                 mock_get_resource_config):
        mock_get_resource_config.return_value = {}
        source_path = get_source_path()

        self.assertIsNone(source_path)

    @patch(f'{pkg}.get_resource_config')
    @patch(f'{pkg}.get_node_instance_dir')
    @patch(f'{pkg}.get_ctx_instance')
    def test_storage_path_not_supported(self,
                                        mock_get_ctx_instance,
                                        mock_get_node_instance_dir,
                                        mock_get_resource_config):
        mock_get_resource_config.return_value = {
            'storage_path': '/different/storage/path'
        }

        mock_get_node_instance_dir.return_value = \
            '/opt/manager/resources/deployments/tenant/deployment_id'

        mock_instance = MagicMock()
        mock_instance.runtime_properties = {}
        mock_get_ctx_instance.return_value = mock_instance

        with self.assertRaises(NonRecoverableError) as context:
            get_storage_path()

        self.assertIn('The property resource_config.storage_path is no longer'
                      ' supported.', str(context.exception))

    @patch(f'{pkg}.get_resource_config')
    @patch(f'{pkg}.get_storage_path')
    def test_plugins_dir_not_subdirectory(self,
                                          mock_get_storage_path,
                                          mock_get_resource_config):
        mock_get_resource_config.return_value = {
            'plugins_dir': '/opt/manager/other_directory/.terraform/plugins'
        }
        mock_get_storage_path.return_value = \
            '/opt/manager/resources/deployments/tenant/deployment_id'

        with self.assertRaises(NonRecoverableError) as context:
            get_plugins_dir()

        self.assertIn('Terraform plugins directory', str(context.exception))
        self.assertIn('must be a subdirectory of the storage_path',
                      str(context.exception))

    @patch(f'{pkg}.get_ctx_instance')
    @patch(f'{pkg}.get_node_instance_dir')
    @patch(f'{pkg}.get_shared_resource')
    @patch(f'{pkg}._create_source_path')
    @patch(f'{pkg}.copy_directory')
    @patch(f'{pkg}.mkdir_p')
    @patch(f'{pkg}.remove_dir')
    def test_get_opa_bundles_with_bundle_tmp_path(self,
                                                  mock_remove_dir,
                                                  mock_mkdir_p,
                                                  mock_copy_directory,
                                                  mock_create_source_path,
                                                  mock_get_shared_resource,
                                                  mock_get_node_instance_dir,
                                                  mock_get_ctx_instance):
        mock_instance = MagicMock()
        mock_instance.runtime_properties = {
            'opa_config': {
                'policy_bundles': [
                    {'location': 'http://example.com/bundle1.zip',
                     'name': 'bundle1'},
                    {'location': 'http://example.com/bundle2.zip',
                     'name': 'bundle2'}
                ]
            }
        }
        mock_get_ctx_instance.return_value = mock_instance
        mock_get_node_instance_dir.return_value = '/tmp/node_instance_dir'

        with tempfile.TemporaryDirectory() as temp_dir:
            bundle_tmp_path1 = os.path.join(temp_dir, 'bundle1')
            os.makedirs(bundle_tmp_path1)
            bundle_file1 = os.path.join(bundle_tmp_path1, 'file.txt')
            with open(bundle_file1, 'w') as f:
                f.write('dummy content')

            bundle_tmp_path2 = os.path.join(temp_dir, 'bundle2')
            os.makedirs(bundle_tmp_path2)
            bundle_file2 = os.path.join(bundle_tmp_path2, 'file.txt')
            with open(bundle_file2, 'w') as f:
                f.write('dummy content')

            mock_get_shared_resource.side_effect = [
                bundle_tmp_path1, bundle_tmp_path2]
            mock_create_source_path.side_effect = [
                (bundle_tmp_path1, None), (bundle_tmp_path2, None)]

            get_opa_bundles()

            expected_policy_dest_path1 = os.path.join(
                '/tmp/node_instance_dir', 'bundle1')
            expected_policy_dest_path2 = os.path.join(
                '/tmp/node_instance_dir', 'bundle2')

            mock_mkdir_p.assert_any_call(expected_policy_dest_path1)
            mock_mkdir_p.assert_any_call(expected_policy_dest_path2)
            self.assertEqual(mock_mkdir_p.call_count, 2)
            mock_copy_directory.assert_any_call(
                bundle_tmp_path1, expected_policy_dest_path1)
            mock_copy_directory.assert_any_call(
                bundle_tmp_path2, expected_policy_dest_path2)
            self.assertEqual(mock_copy_directory.call_count, 2)
            mock_remove_dir.assert_any_call(bundle_tmp_path1)
            mock_remove_dir.assert_any_call(bundle_tmp_path2)
            self.assertEqual(mock_remove_dir.call_count, 2)

    @patch(f'{pkg}.get_resource_config')
    def test_get_repo_url(self, mock_get_resource_config):
        mock_get_resource_config.return_value = {
            'repo_url': 'http://example.com/repo'
        }
        repo_url = get_repo_url()
        expected_repo_url = 'http://example.com/repo'
        self.assertEqual(repo_url, expected_repo_url)

    @patch(f'{pkg}.find_terraform_node_from_rel')
    @patch('os.path.isfile')
    def test_get_binary_location_from_rel(self,
                                          mock_isfile,
                                          mock_find_terraform_node_from_rel):
        mock_isfile.return_value = True
        with patch(f'{pkg}.get_executable_path', return_value=None):
            mock_tf_rel = MagicMock()
            mock_tf_rel.target.node.properties = {
                'terraform_config': {}
            }
            mock_tf_rel.target.instance.runtime_properties = {
                'executable_path': '/mock/path/to/executable'
            }
            mock_find_terraform_node_from_rel.return_value = mock_tf_rel
            result = get_binary_location_from_rel()
            self.assertEqual(result, '/mock/path/to/executable')
