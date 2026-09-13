"""Application Profile domain services.

An application profile is a durable notification namespace.  Profiles share
the Notify Hub database and workers, while channel credentials and user
membership are resolved inside the namespace selected by each source.
"""

from app.profiles.constants import DEFAULT_PROFILE_ID, DEFAULT_PROFILE_KEY

__all__ = ["DEFAULT_PROFILE_ID", "DEFAULT_PROFILE_KEY"]
