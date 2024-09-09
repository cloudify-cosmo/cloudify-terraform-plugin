
try:
    from cloudify import ctx  # noqa: F401
    from cloudify.utils import exception_to_error_cause  # noqa: F401
    from cloudify.exceptions import (  # noqa: F401
        RecoverableError as NON_FATAL_ERR,  # noqa: F401
        NonRecoverableError as FATAL_ERR  # noqa: F401
    )  # noqa: F401
    NE = True
    BINARY_TYPE = 'cloudify.nodes.terraform'
    MODULE_TYPE = 'cloudify.nodes.terraform.Module'
    CREATE = 'cloudify.interfaces.lifecycle.create'
    DELETE = 'cloudify.interfaces.lifecycle.delete'
    CREATE_OP = 'cloudify.interfaces.lifecycle.create'
    REL1 = 'cloudify.terraform.relationships.run_on_host'
    REL2 = 'cloudify.relationships.terraform.run_on_host'
    PRECONFIGURE = 'cloudify.interfaces.relationship_lifecycle.preconfigure'
except ImportError:
    from cloudify import ctx  # noqa: F401
    from cloudify.utils import exception_to_error_cause  # noqa: F401
    from cloudify.exceptions import (  # noqa: F401
        RecoverableError as NON_FATAL_ERR,  # noqa: F401
        NonRecoverableError as FATAL_ERR  # noqa: F401
    )  # noqa: F401
    NE = False
    BINARY_TYPE = 'cloudify.nodes.terraform'
    MODULE_TYPE = 'cloudify.nodes.terraform.Module'
    CREATE = 'cloudify.interfaces.lifecycle.create'
    DELETE = 'cloudify.interfaces.lifecycle.delete'
    CREATE_OP = 'cloudify.interfaces.lifecycle.create'
    REL1 = 'cloudify.terraform.relationships.run_on_host'
    REL2 = 'cloudify.relationships.terraform.run_on_host'
    PRECONFIGURE = 'cloudify.interfaces.relationship_lifecycle.preconfigure'
