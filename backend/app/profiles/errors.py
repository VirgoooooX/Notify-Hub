class ProfileError(Exception):
    code = "PROFILE_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        self.message = message
        if code is not None:
            self.code = code
        super().__init__(message)


class ProfileNotFound(ProfileError):
    code = "PROFILE_NOT_FOUND"


class ProfileDisabled(ProfileError):
    code = "WECOM_PROFILE_DISABLED"


class ProfileNotConfigured(ProfileError):
    code = "PROFILE_NOT_CONFIGURED"


class ProfileSecretMissing(ProfileError):
    code = "WECOM_SECRET_MISSING"


class ProfileCapabilityDisabled(ProfileError):
    code = "PROFILE_CAPABILITY_DISABLED"
