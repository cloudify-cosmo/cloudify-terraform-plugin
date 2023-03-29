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
import json
import base64
import ntpath
import shutil
import zipfile
import filecmp
import tempfile

import requests
import threading
from io import BytesIO
from copy import deepcopy
from textwrap import indent
from itertools import islice
from contextlib import contextmanager

from pathlib import Path
from cloudify import ctx
from cloudify.exceptions import NonRecoverableError, RecoverableError
from cloudify.utils import exception_to_error_cause
from cloudify_common_sdk.hcl import (
    convert_json_hcl,
    extract_hcl_from_dict,
    remove_quotes_from_vars,
)
from cloudify_common_sdk.utils import (
    v1_gteq_v2,
    get_ctx_node,
    download_file,
    copy_directory,
    CommonSDKSecret,
    get_ctx_instance,
    find_rel_by_type,
    with_rest_client,
    get_cloudify_version,
    get_node_instance_dir,
    unzip_and_set_permissions,
    resolve_intrinsic_functions
)
from cloudify_common_sdk.resource_downloader import unzip_archive
from cloudify_common_sdk.resource_downloader import untar_archive
from cloudify_common_sdk.resource_downloader import get_shared_resource
from cloudify_common_sdk.resource_downloader import TAR_FILE_EXTENSTIONS
from cloudify_common_sdk.secure_property_management import (
    store_property,
    get_stored_property)


try:
    from cloudify.constants import RELATIONSHIP_INSTANCE, NODE_INSTANCE
except ImportError:
    NODE_INSTANCE = 'node-instance'
    RELATIONSHIP_INSTANCE = 'relationship-instance'

from .constants import (
    NAME,
    STATE,
    DRIFTS,
    IS_DRIFTED,
    TERRAFORM_STATE_FILE
)
from ._compat import text_type, StringIO, mkdir_p


def convert_secrets(data):
    def recurse(x):
        if isinstance(x, CommonSDKSecret):
            return x.secret
        elif isinstance(x, dict):
            new_dict = {}
            for k, v in x.items():
                new_dict[k] = recurse(v)
            return new_dict
        elif isinstance(x, list):
            new_list = []
            for i in x:
                new_list.append(recurse(i))
            return new_list
        return x
    return recurse(deepcopy(data))


def exclude_file(dirname, filename, excluded_files):
    """In _zip_archive, we need to prevent certain files, i.e. the TF binary,
    from being added  to the zip. It's totally unnecessary,
    and also crashes the manager.
    """
    rel_path = os.path.join(dirname, filename)
    for f in excluded_files:
        if not f:
            continue
        elif os.path.isfile(f) and rel_path == f:
            return True
        elif os.path.isdir(f) and f in rel_path:
            return True
    return False


def exclude_dirs(dirname, subdirs, excluded_files):
    """In _zip_archive, we need to prevent certain files, i.e. TF plugins,
    from being added  to the zip. It's totally unnecessary,
    and also crashes the manager.
    """
    rel_subdirs = [os.path.join(dirname, d) for d in subdirs]
    for f in excluded_files:
        if not f:
            continue
        if os.path.isdir(f) and f in rel_subdirs:
            try:
                subdirs.remove(ntpath.basename(f))
            except ValueError:
                pass


def file_storage_breaker(filepath):
    filesize = Path(filepath).stat().st_size
    if filesize > 50000:
        max_stored_filesize = get_ctx_node().properties.get(
            'max_stored_filesize', 500000)
        if filesize >= max_stored_filesize:
            ctx.logger.warn(
                'The file {f} exceeds max file size {s} '
                'set in the max_stored_filesize property: {m}'
                .format(f=filepath, s=filesize, m=max_stored_filesize))
            return True
    elif '.terraform/plugins' in filepath:
        store_plugins_dir = get_ctx_node().properties.get(
            'store_plugins_dir', False)
        if not store_plugins_dir:
            ctx.logger.warn(
                'Not preserving file {f}, '
                'because it is in plugins directory.'.format(f=filepath))
            return True
    return False


def _zip_archive(extracted_source, exclude_files=None, **_):
    """Zip up a folder and all its sub-folders,
    except for those that we wish to exclude.

    :param extracted_source: The location.
    :param exclude_files: A list of files and directories, that we don't
    want to put in the zip.
    :param _:
    :return:
    """
    ctx.logger.debug("Zipping source {source}".format(source=extracted_source))
    exclude_files = exclude_files or []
    ctx.logger.debug('Excluding files {l}'.format(l=exclude_files))
    with tempfile.NamedTemporaryFile(suffix=".zip",
                                     delete=False) as updated_zip:
        updated_zip.close()
        with zipfile.ZipFile(updated_zip.name,
                             mode='w',
                             compression=zipfile.ZIP_DEFLATED) as output_file:
            for dir_name, subdirs, filenames in os.walk(extracted_source):
                # Make sure that the files that we don't want
                # to include (e.g. plugins directory) will not be archived.
                exclude_dirs(dir_name, subdirs, exclude_files)
                for filename in filenames:
                    # Extra layer of validation on the excluded files.
                    if not exclude_file(dir_name, filename, exclude_files):
                        # Create the path as we want to archive it to the
                        # archivee.
                        file_to_add = os.path.join(dir_name, filename)
                        # The name of the file in the archive.
                        if file_storage_breaker(file_to_add):
                            continue
                        arc_name = file_to_add[len(extracted_source)+1:]
                        output_file.write(file_to_add, arcname=arc_name)
        archive_file_path = updated_zip.name
    return archive_file_path


def _unzip_archive(archive_path, target_directory, source_path=None, **_):
    """
    Unzip a zip archive.
    """

    # Create a temporary directory.
    # Create a zip archive object.
    # Extract the object.
    ctx.logger.debug('Unzipping {src} to {dst}.'.format(
        src=archive_path, dst=target_directory))

    src = unzip_archive(archive_path, skip_parent_directory=False)
    copy_directory(src, target_directory)
    remove_dir(src)
    return target_directory


def clean_strings(string):
    if isinstance(string, text_type):
        return string.encode('utf-8').rstrip("'").lstrip("'")
    return string


def _file_to_base64(file_path):
    # By getting here, "terraform_source_zip" is the path to a ZIP
    # file containing the Terraform files.
    # We need to encode the contents of the file and set them
    # as a runtime property.
    base64_rep = BytesIO()
    with open(file_path, 'rb') as f:
        base64.encode(f, base64_rep)
    return base64_rep.getvalue().decode('utf-8')


def _create_source_path(source_tmp_path):
    # didn't download anything so check the provided path
    # if file and absolute path or not
    delete_tmp = False
    if not os.path.isabs(source_tmp_path):
        # bundled and need to be downloaded from blueprint
        source_tmp_path = ctx.download_resource(source_tmp_path)
        # the above source_tmp_path would be temp_dir/downloaded_file
        # so when deleting that let's delete the parent
        delete_tmp = True

    # only allow delete if we downloaded a bundled archive not local archives
    if os.path.isfile(source_tmp_path):
        file_name = source_tmp_path.rsplit('/', 1)[1]
        file_type = file_name.rsplit('.', 1)[1]
        # check type
        if file_type == 'zip':
            unzipped_source = unzip_archive(source_tmp_path, False)
            if delete_tmp:
                remove_dir(os.path.dirname(source_tmp_path))
            source_tmp_path = unzipped_source
        elif file_type in TAR_FILE_EXTENSTIONS:
            unzipped_source = untar_archive(source_tmp_path, False)
            if delete_tmp:
                remove_dir(os.path.dirname(source_tmp_path))
            source_tmp_path = unzipped_source
        elif file_type == 'json' and 'tf.json' in file_name:
            if delete_tmp:
                source_tmp_path = os.path.dirname(source_tmp_path)
    return source_tmp_path, delete_tmp


def is_using_existing(target=True):
    """Decide if we need to do this work or not."""
    resource_config = get_resource_config(target=target)
    if not target:
        tf_rel = find_terraform_node_from_rel()
        if tf_rel:
            resource_config = tf_rel.target.instance.runtime_properties.get(
                'resource_config', {})
    return resource_config.get('use_existing_resource', True)


def get_binary_location_from_rel():
    candidate_b = get_executable_path()
    if candidate_b and os.path.isfile(candidate_b):
        ctx.logger.debug(
            'Executable path from node {}'.format(candidate_b))
        return candidate_b
    tf_rel = find_terraform_node_from_rel()
    if tf_rel:
        terraform_config = tf_rel.target.node.properties.get(
            'terraform_config', {})
        candidate_a = terraform_config.get('executable_path')
        if candidate_a and os.path.isfile(candidate_a):
            ctx.logger.debug(
                'Executable path from rel {}'.format(candidate_a))
            return candidate_a
    raise NonRecoverableError(
        "Terraform's executable not found from relationship "
        "'cloudify.terraform.relationships.run_on_host' to type "
        "'cloudify.nodes.terraform' or by setting the value in "
        "'terraform_config'. Please set the 'executable_path' property "
        "accordingly.")


def find_terraform_node_from_rel():
    return find_rel_by_type(
        ctx.instance, 'cloudify.terraform.relationships.run_on_host')


def update_resource_config(new_values, target=False):
    store_property(ctx, 'resource_config', new_values, target)


def get_resource_config(target=False, force=None):
    """Get the cloudify.nodes.terraform.Module resource_config"""
    return get_stored_property(ctx, 'resource_config', target, force)


def get_repo_url():
    rc = get_resource_config()
    return rc.get('repo_url')


def get_provider_upgrade(target=False):
    """Get the cloudify.nodes.terraform.Module provider_upgrade"""
    instance = get_ctx_instance(target=target)
    provider_upgrade = instance.runtime_properties.get('provider_upgrade')
    if not provider_upgrade:
        node = get_ctx_node(target=target)
        provider_upgrade = node.properties.get('provider_upgrade', False)
    return provider_upgrade


def get_terraform_config(target=False):
    """get the cloudify.nodes.terraform or cloudify.nodes.terraform.Module
    terraform_config"""
    instance = get_ctx_instance(target=target)
    terraform_config = instance.runtime_properties.get('terraform_config')
    if terraform_config:
        return terraform_config
    node = get_ctx_node(target=target)
    return node.properties.get('terraform_config', {})


def update_terraform_source_material(new_source, target=False):
    """Replace the terraform_source material with a new material.
    This is used in terraform.reload_template operation."""
    ctx.logger.debug('update_terraform_source_material '
                     'new_source: {}'.format(new_source))
    delete_tmp = True
    new_source_location = new_source
    new_source_username = None
    new_source_password = None
    node_instance_dir = get_node_instance_dir(target=target)
    if isinstance(new_source, dict):
        new_source_location = new_source['location']
        new_source_username = new_source.get('username')
        new_source_password = new_source.get('password')
        ctx.logger.debug('Getting shared resource: {} to {}'.format(
            new_source_location, node_instance_dir))
    if isinstance(new_source_username, CommonSDKSecret):
        new_source_username = new_source_username.secret
    if isinstance(new_source_password, CommonSDKSecret):
        new_source_password = new_source_password.secret
    source_tmp_path = get_shared_resource(
        new_source_location, dir=node_instance_dir,
        username=new_source_username,
        password=new_source_password)

    ctx.logger.debug('Source temp path {}'.format(source_tmp_path))
    # check if we actually downloaded something or not
    if source_tmp_path == new_source_location:
        source_tmp_path, delete_tmp = _create_source_path(source_tmp_path)
    # move tmp files to correct directory if tmp_path is directory otherwise
    # the resources will be downloaded inside the node_instance_dir
    if os.path.isdir(source_tmp_path):
        copy_directory(source_tmp_path, node_instance_dir)

    # By getting here we will have extracted source
    # Zip the file to store in runtime
    if not v1_gteq_v2(get_cloudify_version(), "6.0.0"):
        terraform_source_zip = _zip_archive(source_tmp_path)
        bytes_source = _file_to_base64(terraform_source_zip)
        os.remove(terraform_source_zip)
        # check if skip_parent_directory was executed
        # reason for that 4 is because in general /tmp/tmpXXXX/single_folder
        # so let's take the parent to clean up properly
        if len(source_tmp_path.split(os.sep)) == 4:
            source_tmp_path = os.path.dirname(source_tmp_path)

        if os.path.abspath(os.path.dirname(source_tmp_path)) != \
                os.path.abspath(node_instance_dir) and delete_tmp:
            remove_dir(source_tmp_path)

        return bytes_source
    else:
        # check if skip_parent_directory was executed
        # reason for that 4 is because in general /tmp/tmpXXXX/single_folder
        # so let's take the parent to clean up properly
        if len(source_tmp_path.split(os.sep)) == 4:
            source_tmp_path = os.path.dirname(source_tmp_path)

        if os.path.abspath(os.path.dirname(source_tmp_path)) != \
                os.path.abspath(node_instance_dir) and delete_tmp:
            remove_dir(source_tmp_path)


def get_terraform_source_material(target=False):
    """In principle this is the binary data of a zip archive containing the
    Terraform state and plan files.
    However, during the install workflow, this might also be the binary
    data of a zip archive of just the plan files.
    """
    instance = get_ctx_instance(target=target)
    terraform_source = instance.runtime_properties.get('terraform_source')
    resource_config = get_resource_config(target=target)
    source = resource_config.get('source')
    if not isinstance(source, dict):
        resource_config.update({'source': source})
    if not terraform_source:
        terraform_source = update_terraform_source_material(
            source, target=target)
    update_resource_config(resource_config)
    return terraform_source


def get_opa_bundles(target=False):
    """Downloads OPA bundles to the Terraform module's directory and stores
    them in a subdirectory according to their name.
    """
    instance = get_ctx_instance(target=target)
    opa_config = instance.runtime_properties.get('opa_config')
    policy_bundles = opa_config['policy_bundles']
    node_instance_dir = get_node_instance_dir(target=target)

    # Download each bundle zip file to a temporary directory
    for bundle in policy_bundles:
        source_location = bundle['location']
        source_username = bundle.get('username')
        source_password = bundle.get('password')
        if isinstance(source_username, CommonSDKSecret):
            source_username = source_username.secret
        if isinstance(source_password, CommonSDKSecret):
            source_password = source_password.secret
        bundle_tmp_path = get_shared_resource(
            source_location, dir=node_instance_dir,
            username=source_username,
            password=source_password)

        bundle_tmp_path, _ = _create_source_path(bundle_tmp_path)
        ctx.logger.debug("OPA bundle temp path {}".format(bundle_tmp_path))

        # Copy the tempfiles into the node instance directory, preserving the
        # directory structure
        if os.path.isdir(bundle_tmp_path):
            policy_dest_path = os.path.join(node_instance_dir, bundle['name'])
            ctx.logger.debug("Moving OPA bundle {} to {}"
                             .format(bundle['name'], policy_dest_path))
            mkdir_p(policy_dest_path)
            copy_directory(bundle_tmp_path, policy_dest_path)

        remove_dir(bundle_tmp_path)


def get_installation_source(target=False):
    """This is the URL or file where we can get the Terraform binary"""
    resource_config = get_resource_config(target=target)
    source = resource_config.get('installation_source')
    if not source:
        raise NonRecoverableError(
            'No download URL for terraform binary executable file was '
            'provided and use_external_resource is False. '
            'Please provide a valid download URL.')
    return source


def get_executable_path(target=False):
    """The Terraform binary executable.
    It should either be: null, in which case it defaults to
    /opt/manager/resources/deployments/{tenant}/{deployment_id}/terraform
    or it will be /usr/bin/terraform, and this should be used as an
    existing resource.
    Any other value will probably not work for the user.
    """
    instance = get_ctx_instance(target=target)
    initial_executable_path = \
        instance.runtime_properties.get('executable_path')
    executable_path = initial_executable_path
    if not initial_executable_path:
        terraform_config = get_terraform_config(target=target)
        executable_path = terraform_config.get('executable_path') or \
            os.path.join(get_node_instance_dir(target=target), 'terraform')
    if initial_executable_path and \
            not os.path.exists(initial_executable_path) \
            and is_using_existing(target=target):
        # executable path set by relationship precreate operation
        raise RecoverableError(
            "If executable_path {} does not exist and there is no "
            "executable_path in terraform_config there is a need "
            "to retry and wait for file system to sync.".format(
                initial_executable_path
            ))
    if not os.path.exists(executable_path) and \
            is_using_existing(target=target):
        node = get_ctx_node(target=target)
        terraform_config = node.properties.get('terraform_config', {})
        executable_path = terraform_config.get('executable_path')
    if not initial_executable_path:
        instance.runtime_properties['executable_path'] = executable_path
    return executable_path


def get_storage_path(target=False):
    """Where we install all of our terraform files.
    It should always be: /opt/manager/resources/deployments/{tenant}
    /{deployment_id}
    """
    resource_config = get_resource_config(target=target)
    deployment_dir = get_node_instance_dir(target=target)
    storage_path = resource_config.get('storage_path')
    if storage_path and storage_path is not deployment_dir:
        raise NonRecoverableError(
            'The property resource_config.storage_path '
            'is no longer supported.')
    instance = get_ctx_instance(target=target)
    instance.runtime_properties['storage_path'] = deployment_dir
    return deployment_dir


def get_plugins_dir(target=False):
    """Plugins are installed into this directory.
    It should always be: /opt/manager/resources/deployments/{tenant}
    /{deployment_id}/.terraform/plugins
    """
    resource_config = get_resource_config(target=target)
    storage_path = get_storage_path(target=target)
    source_path = get_source_path(target=target)
    if source_path:
        storage_path = os.path.join(storage_path, source_path)
    plugins_dir = resource_config.get(
        'plugins_dir',
        os.path.join(storage_path, '.terraform', 'plugins'))
    if storage_path not in plugins_dir:
        raise NonRecoverableError(
            'Terraform plugins directory {plugins} '
            'must be a subdirectory of the storage_path {storage}.'.format(
                plugins=plugins_dir, storage=storage_path))
    return plugins_dir


def get_plugins(target=False):
    """These are plugins that the user wishes to install."""
    resource_config = get_resource_config(target=target)
    return resource_config.get('plugins', {})


def get_source_path(target=False):
    resource_config = get_resource_config(target=target)
    source_path = resource_config.get('source_path')
    source = resource_config.get('source', {})
    if 'source_path' in source:
        source_path = source['source_path']
    return source_path


def update_source_path(source_path=None):
    resource_config = get_resource_config()
    source = resource_config.get('source', {})
    resource_config['source_path'] = source_path
    source.update({'source_path': source_path})
    resource_config['source'] = source
    return resource_config


def create_plugins_dir(plugins_dir=None):
    """Create the directory where we will install all the plugins."""
    # Create plugins directory, if needed.
    if plugins_dir:
        if os.path.isdir(plugins_dir):
            ctx.logger.error('Plugins directory already exists: {loc}'.format(
                loc=plugins_dir))
        else:
            ctx.logger.error('Creating plugins directory: {loc}'.format(
                loc=plugins_dir))
            mkdir_p(plugins_dir)
        # store the values in the runtime for safe keeping -> validation
        ctx.instance.runtime_properties['plugins_dir'] = plugins_dir


def remove_dir(folder, desc=''):
    if os.path.islink(folder):
        ctx.logger.debug('Unlinking: {}'.format(folder))
        os.unlink(folder)
    elif os.path.isdir(folder):
        if len(folder.split(os.sep)) < 3:
            return
        ctx.logger.debug(
            'Removing {desc}: {dir}'.format(desc=desc, dir=folder))
        try:
            shutil.rmtree(folder)
        except OSError as e:
            ctx.logger.error(
                'Unable to safely remove temporary extraction of archive. '
                'Error: {}'.format(str(e)))
    elif os.path.isfile(folder):
        ctx.logger.debug(
            'Removing file {dir}'.format(dir=folder))
        os.remove(folder)
    else:
        ctx.logger.debug(
            'Directory {dir} doesn\'t exist; skipping'.format(dir=folder))


def handle_plugins(plugins, plugins_dir, installation_dir):
    """Create the directory where we will download requested plugins into,
    and then download them into it."""
    create_plugins_dir(plugins_dir)
    # Install plugins.
    if not isinstance(plugins, dict):
        raise NonRecoverableError(
            'The plugins value is not valid: {value} '
            'If you wish to use custom Terraform providers must provide a '
            'dictionary in the following format: search.path/provider_name.'
            ''
            'For example:'
            'plugins: \n'
            '  registry.terraform.io/hashicorp/template: '
            'https://releases.hashicorp.com/terraform-provider-template/'
            '2.1.2/'
            'terraform-provider-template_2.1.2_linux_amd64.zip\n'.format(
                value=plugins)
        )
    for plugin_name, plugin_url in plugins.items():
        with tempfile.NamedTemporaryFile(
                suffix=".zip",
                delete=False,
                dir=installation_dir) as plugin_zip:
            plugin_zip.close()
            ctx.logger.debug('Downloading Terraform plugin: {url}'.format(
                url=plugin_url))
            download_file(plugin_zip.name, plugin_url)
            unzip_path = os.path.join(plugins_dir, plugin_name)
            mkdir_p(os.path.dirname(unzip_path))
            unzip_and_set_permissions(plugin_zip.name, unzip_path)
            os.remove(plugin_zip.name)


def dump_file(output, work_directory, file_name):
    if output:
        file_path = os.path.join(work_directory, file_name)
        fd = open(file_path, 'w')
        fd.write(output)
        fd.close()


def extract_binary_tf_data(root_dir, data, source_path):
    """Take this encoded data and put it in a zip file and then unzip it."""
    with tempfile.NamedTemporaryFile(dir=root_dir, delete=False) as f:
        base64.decode(StringIO(data), f)
        terraform_source_zip = f.name
    # By getting here, "terraform_source_zip" is the path
    #  to a ZIP file containing the Terraform files.
    _unzip_archive(terraform_source_zip, root_dir, source_path)


@contextmanager
def get_terraform_source():
    """Get the JSON/TF files material for the Terraform template.
    Dump in in the file yielded by _yield_terraform_source
    """
    material = get_terraform_source_material()
    return _yield_terraform_source(material)


def update_terraform_source(new_source=None, new_source_path=None):
    """Replace the stored terraform resource template data"""
    if new_source:
        material = update_terraform_source_material(new_source)
    else:
        # If the plan operation passes NOone, then this would error.
        material = get_terraform_source_material()
    node_instance_dir = get_node_instance_dir(source_path=new_source_path)
    module_root = get_storage_path()
    ctx.logger.debug('The storage root tree:\n{}'.format(tree(module_root)))
    if material:
        extract_binary_tf_data(module_root, material, new_source_path)
    else:
        if isinstance(new_source, str) and os.path.isdir(new_source):
            if new_source_path:
                source_tmp_path = os.path.join(new_source, new_source_path)
            else:
                source_tmp_path = new_source
            copy_directory(source_tmp_path, node_instance_dir)
        elif isinstance(new_source, dict) and os.path.isdir(
                new_source.get('location')):
            if new_source_path:
                source_tmp_path = os.path.join(
                    new_source.get('location'), new_source_path)
            else:
                source_tmp_path = new_source.get('location')
            copy_directory(source_tmp_path, node_instance_dir)
    return node_instance_dir


def _yield_terraform_source(material, source_path=None):
    """Put all the TF resource template data into the work directory,
    let the operations do all their magic,
    and then store it again for later use.
    """
    module_root = get_storage_path()
    source_path = source_path or get_source_path()
    if material:
        extract_binary_tf_data(module_root, material, source_path)
    ctx.logger.debug(
        'The storage root tree:\n{}'.format(tree(module_root)))
    path_to_init_dir = get_node_instance_dir(source_path=source_path)
    try:
        ctx.logger.debug('_yield_terraform_source {}'.format(path_to_init_dir))
        yield path_to_init_dir
    except Exception as e:
        _, _, tb = sys.exc_info()
        cause = exception_to_error_cause(e, tb)
        ctx.logger.error(str(cause))
        raise e
    finally:
        if v1_gteq_v2(get_cloudify_version(), "6.0.0"):
            ctx.logger.debug('Not storing zip in runtime properties in '
                             'Cloudify 6.0.0 and greater')
        else:
            store_binary_material(module_root)
    ctx.instance.runtime_properties['previous_tf_state_file'] = \
        get_terraform_state_file(path_to_init_dir)


def store_binary_material(module_root, ):
    ctx.logger.debug('Re-packaging Terraform files from {loc}'.format(
        loc=module_root))
    archived_file = _zip_archive(
        module_root,
        exclude_files=[get_executable_path(),
                       get_plugins_dir()])
    # Convert the zip archive into base64 for storage in runtime
    # properties.
    base64_rep = _file_to_base64(archived_file)
    os.remove(archived_file)

    ctx.logger.warn('The after base64_rep size is {size}.'.format(
        size=len(base64_rep)))

    if len(base64_rep) > get_ctx_node().properties.get(
            'max_runtime_property_size', 100000):
        raise Exception('Not storing terraform_source, '
                        'because its size is {}'.format(len(base64_rep)))
    else:
        ctx.logger.info('Storing zip "terraform source" in '
                        'runtime properties.')
        ctx.instance.runtime_properties['terraform_source'] = \
            base64_rep


def try_to_copy_old_state_file(target_dir):
    p = ctx.instance.runtime_properties.get(
        'previous_tf_state_file')

    if p and os.path.exists(p) and os.stat(p).st_size:
        target_file = os.path.join(target_dir, os.path.basename(p))
        try:
            os.symlink(p, target_file)
        except OSError:
            ctx.logger.warn('Unable to link {src} {dst}'.format(
                src=p, dst=target_file))
    ctx.instance.runtime_properties['previous_tf_state_file'] = p


def get_terraform_state_file(target_dir=None):
    """Create or dump the state. This is only used in the
    terraform.refresh_resources operations and it's possible we can
    get rid of it.
    """

    if isinstance(target_dir, dict):
        target_dir = target_dir.get('location')

    if isinstance(target_dir, str):
        for p in os.listdir(target_dir):
            if p.endswith('tfstate'):
                return os.path.join(target_dir, p)

    storage_path = get_storage_path()
    state_file_path = os.path.join(storage_path, TERRAFORM_STATE_FILE)
    encoded_source = get_terraform_source_material()
    source_path = get_source_path()

    # with tempfile.NamedTemporaryFile(delete=False) as f:
    with tempfile.NamedTemporaryFile() as f:
        base64.decode(StringIO(encoded_source), f)
        try:
            extracted_source = _unzip_archive(f.name,
                                              storage_path,
                                              source_path)
        except zipfile.BadZipFile:
            extracted_source = None

    if not extracted_source:
        return

    for dir_name, subdirs, filenames in os.walk(extracted_source):
        for filename in filenames:
            if filename == TERRAFORM_STATE_FILE:
                state_file_from_storage = os.path.join(dir_name, filename)
                if not os.path.exists(state_file_path):
                    ctx.logger.warn(
                        'There is no existing state file {loc}.'.format(
                            loc=state_file_path))
                if not filecmp.cmp(state_file_from_storage, state_file_path):
                    ctx.logger.warn(
                        'State file from storage is not the same as the '
                        'existing state file {loc}. Using any way.'.format(
                            loc=state_file_path))
                shutil.move(os.path.join(dir_name, filename), state_file_path)
                break

    shutil.rmtree(extracted_source)
    ctx.logger.debug('TF State file: {}.'.format(state_file_path))
    return state_file_path


def create_backend_string(name, options):
    name = name.strip('"')
    backend_block = convert_json_hcl(extract_hcl_from_dict(
        {
            'type_name': 'backend',
            'option_name': name,
            'option_value': options
        }
    ))
    terraform_block = 'terraform {{\n{}}}'.format(
        indent(backend_block, '    '))
    return remove_quotes_from_vars(terraform_block)


def create_required_providers_string(items):
    required_providers = {
        "terraform": {
            "required_providers": items
        }
    }
    return json.dumps(required_providers, indent=4)


def create_provider_string(items):
    provider = ""
    for item in items:
        provider += convert_json_hcl(extract_hcl_from_dict(
          {
            'type_name': 'provider',
            'option_name': item.get('name').strip('"'),
            'option_value': item.get('options')
          }
        ))
        provider += "\n"
    return provider


def filter_state_for_sensitive_properties(output):
    resource_config = get_resource_config(target=False)
    ret = dict()
    for k in output.keys():
        is_sensitive_ouput = output[k].get("sensitive", False)
        obfuscate_sensitive = resource_config.get('obfuscate_sensitive')
        is_in_store_output_secrets = \
            k in resource_config.get('store_output_secrets', {})
        if (is_sensitive_ouput and obfuscate_sensitive) or \
                (is_sensitive_ouput and is_in_store_output_secrets):
            ret[k] = "*" * 10
        else:
            ret[k] = output[k]
    return ret


@with_rest_client
def store_sensitive_properties(rest_client, output):
    resource_config = get_resource_config(target=False)
    store_output_secrets = resource_config.get("store_output_secrets", {})
    for output_k in store_output_secrets.keys():
        output_val = output.get(output_k).get('value')
        if not isinstance(output_val, str):
            output_val = json.dumps(output_val)
        rest_client.secrets.create(key=store_output_secrets[output_k],
                                   value=output_val,
                                   update_if_exists=True)


def refresh_resources_properties(state, output, update_runtime_props=True):
    """Store all the resources (state and output) that we created as JSON
       in the context."""
    resources = {}
    for resource in state.get('resources', []):
        resources[resource[NAME]] = resource
    for module in state.get('modules', []):
        for name, definition in module.get('resources', {}).items():
            resources[name] = definition
    if update_runtime_props:
        ctx.instance.runtime_properties['resources'] = resources
    # Duplicate for backward compatibility.
        ctx.instance.runtime_properties[STATE] = resources
        ctx.instance.runtime_properties['outputs'] = \
            filter_state_for_sensitive_properties(output)
        store_sensitive_properties(output=output)


def refresh_resources_drifts_properties(plan_json):
    """
        Store all drifts(changes) in resources we created in runtime
        properties.
        The change represent the difference between the current state and
        the desired state.
        The desired state is the infrastructure crated from terraform source.
        means that the tf template is the source of truth.
        :param plan_json: dictionary represent json output of plan,
        as described
        here: https://www.terraform.io/docs/internals/json-format.html#plan
        -representation
    """
    ctx.instance.runtime_properties[IS_DRIFTED] = False
    drifts = {}
    resource_changes = plan_json.get('resource_changes', [])
    for resource_change in resource_changes:
        change = resource_change.get('change', {})
        if change['actions'] not in [['no-op'], ['read']]:
            ctx.instance.runtime_properties[IS_DRIFTED] = True
            drifts[resource_change[NAME]] = change
    ctx.instance.runtime_properties[DRIFTS] = drifts


def is_url(string):
    try:
        result = requests.get(string)
    except requests.ConnectionError:
        return False
    if result.status_code == 404:
        ctx.logger.warn('The source {source} is a valid URL, '
                        'but is not found.'.format(source=string))
    return True


def handle_previous_source_format(source):
    ctx.logger.info('Source: {}'.format(source))
    if isinstance(source, dict):
        return source
    elif isinstance(source, str) and source.startswith('/'):
        return {'location': source}
    elif isinstance(source, str) and is_url(source):
        return {'location': source}
    try:
        return json.loads(source)
    except (ValueError, TypeError):
        if is_url(source):
            return {'location': source}
    return source


# Stolen from the script plugin, until this class
# moves to a utils module in cloudify-common.
class OutputConsumer(object):
    def __init__(self, out):
        self.out = out
        self.consumer = threading.Thread(target=self.consume_output)
        self.consumer.daemon = True

    def consume_output(self):
        for line in self.out:
            self.handle_line(line)
        self.out.close()

    def handle_line(self, line):
        raise NotImplementedError("Must be implemented by subclass")

    def join(self):
        self.consumer.join()


class LoggingOutputConsumer(OutputConsumer):
    def __init__(self, out, logger, prefix):
        OutputConsumer.__init__(self, out)
        self.logger = logger
        self.prefix = prefix
        self.consumer.start()

    def handle_line(self, line):
        self.logger.info('{0}{1}'.format(text_type(self.prefix),
                                         line.decode('utf-8').rstrip('\n')))


class CapturingOutputConsumer(OutputConsumer):
    def __init__(self, out):
        OutputConsumer.__init__(self, out)
        self.buffer = StringIO()
        self.consumer.start()

    def handle_line(self, line):
        self.buffer.write(line.decode('utf-8'))

    def get_buffer(self):
        return self.buffer


def tree(dir_path, level=-1, limit_to_directories=False, length_limit=1000000):
    """Stolen from here: https://stackoverflow.com/questions/9727673/
    list-directory-tree-structure-in-python

    :param dir_path:
    :param level:
    :param limit_to_directories:
    :param length_limit:
    :return:
    """

    space = '    '
    branch = '│   '
    tee = '├── '
    last = '└── '

    if not isinstance(dir_path, Path):
        dir_path = Path(dir_path)

    files = 0
    directories = 0
    tree_lines = []

    def inner(dir_path, prefix=None, level=-1):

        prefix = prefix or ''

        nonlocal files, directories
        if not level:
            return
        if limit_to_directories:
            contents = [d for d in dir_path.iterdir() if d.is_dir()]
        else:
            contents = list(dir_path.iterdir())
        pointers = [tee] * (len(contents) - 1) + [last]
        for pointer, path in zip(pointers, contents):
            if path.is_dir():
                yield prefix + pointer + path.name
                directories += 1
                extension = branch if pointer == tee else space
                yield from inner(path, prefix=prefix+extension, level=level-1)
            elif not limit_to_directories:
                yield prefix + pointer + path.name
                files += 1
    tree_lines.append(dir_path.name)
    iterator = inner(dir_path, level=level)
    for line in islice(iterator, length_limit):
        tree_lines.append(line)
    if next(iterator, None):
        tree_lines.append(
            '... length_limit, {}, reached, counted:'.format(length_limit))
    tree_lines.append(
        '\n{} directories'.format(directories) + (
            ', {} files'.format(files) if files else ''))
    return '\n'.join(tree_lines)


def resolve_dict_intrinsic_vals(dict_val, dep_id):
    # could be intrinsic function directly
    resolved_dict = resolve_intrinsic_functions(dict_val, dep_id)
    if isinstance(resolved_dict, dict):
        # could be dict of dicts with intrinsic functions
        resolved_dict = {}
        for k, v in dict_val.items():
            resolved_val = resolve_intrinsic_functions(v, dep_id)
            resolved_dict[k] = resolved_val
    return resolved_dict


def first_merge_in_second(new, original):
    if new and original:
        for key, value in new.items():
            original[key] = value
    return original
