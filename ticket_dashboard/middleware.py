# TODO: Remove after SSO debugging is complete
import logging

logger = logging.getLogger(__name__)


class SSODebugMiddleware:
    """Temporary middleware to debug SSO callback issues in production."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if "/accounts/" in request.path:
            logger.info(
                "SSO debug [%s %s]: is_secure=%s, X-Forwarded-Proto=%s, "
                "Host=%s, session_key=%s, cookie_names=%s",
                request.method,
                request.path,
                request.is_secure(),
                request.headers.get("x-forwarded-proto"),
                request.headers.get("host"),
                request.session.session_key,
                list(request.COOKIES.keys()),
            )
        return self.get_response(request)
