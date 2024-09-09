
"""Python 2 + 3 compatibility utils"""
# flake8: noqa

import sys

PY2 = sys.version_info[0] == 2

if PY2:
    from StringIO import StringIO
    exec ("""
def reraise(exception_type, value, traceback):
    raise exception_type, value, traceback
""")
    text_type = unicode
    exec ("""
def exec_(code, globs):
    exec code in globs
""")
    PermissionDenied = OSError


    def mkdir_p(path):
        import os
        try:
            os.makedirs(path)
        except OSError:
            if not os.path.isdir(path):
                raise
else:
    import builtins
    from io import StringIO


    def reraise(exception_type, value, traceback):
        raise value.with_traceback(traceback)


    text_type = str
    exec_ = getattr(builtins, 'exec')
    PermissionDenied = PermissionError

    def mkdir_p(path):
        import pathlib
        pathlib.Path(path).mkdir(parents=True, exist_ok=True)

__all__ = [
    'PY2', 'StringIO', 'reraise', 'text_type', 'exec_', 'PermissionDenied',
    'mkdir_p']
