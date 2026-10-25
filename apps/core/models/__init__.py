"""Model package: custom user plus all legacy schema models."""

from .user import User
from .system import *  # noqa: F401,F403
from .etims import *  # noqa: F401,F403
from .documents import *  # noqa: F401,F403
from .projects import *  # noqa: F401,F403
from .licensing import *  # noqa: F401,F403
from .sales import *  # noqa: F401,F403
from .crm import *  # noqa: F401,F403
from .accounting import *  # noqa: F401,F403
from .inventory import *  # noqa: F401,F403
from .purchasing import *  # noqa: F401,F403
from .hr import *  # noqa: F401,F403
