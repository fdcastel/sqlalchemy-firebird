from sqlalchemy.dialects import registry
import pytest

# setup default dialect for sqlalchemy
registry.register(
    "firebird", "sqlalchemy_firebird.firebird", "FBDialect_firebird"
)
registry.register(
    "firebird.firebird", "sqlalchemy_firebird.firebird", "FBDialect_firebird"
)

pytest.register_assert_rewrite("sqlalchemy.testing.assertions")

# this happens after pytest.register_assert_rewrite to avoid pytest warning
from sqlalchemy.testing.plugin.pytestplugin import *  # noqa: F401, E402, F403
