"""
Middleware: добавляет заголовки безопасности к каждому ответу.

- Referrer-Policy: ограничивает передачу Referer
- Content-Security-Policy: по режиму API_CSP_MODE / профилю (С11)
- Permissions-Policy: отключает неиспользуемые браузерные API
"""


def _csp_header() -> str:
    from src.config.security_profile_runtime import security_env_str
    from security.csp_policy import build_csp_policy, normalize_csp_mode

    mode = normalize_csp_mode(security_env_str('API_CSP_MODE', default='as_is'))
    return build_csp_policy(mode)


class SecurityHeadersMiddleware:
    """
    Добавляет к ответу заголовки Referrer-Policy, Content-Security-Policy, Permissions-Policy.
    Должен идти после SecurityMiddleware.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not hasattr(response, "headers"):
            return response

        if "Referrer-Policy" not in response.headers:
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        if "Content-Security-Policy" not in response.headers:
            response.headers["Content-Security-Policy"] = _csp_header()

        if "Permissions-Policy" not in response.headers:
            response.headers["Permissions-Policy"] = (
                "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
                "magnetometer=(), microphone=(), payment=(), usb=()"
            )

        return response
