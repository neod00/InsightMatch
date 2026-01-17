# core/__init__.py
"""
Core utilities for InsightMatch API.
"""

from .responses import (
    success_response,
    error_response,
    not_found,
    unauthorized,
    forbidden,
    bad_request,
    server_error,
    validation_error,
    handle_exceptions,
    ERROR_CODES
)

from .handlers import (
    AuthHandler,
    ConsultantHandler,
    MatchingHandler,
    ProjectHandler
)

__all__ = [
    # Responses
    'success_response',
    'error_response',
    'not_found',
    'unauthorized',
    'forbidden',
    'bad_request',
    'server_error',
    'validation_error',
    'handle_exceptions',
    'ERROR_CODES',
    # Handlers
    'AuthHandler',
    'ConsultantHandler',
    'MatchingHandler',
    'ProjectHandler'
]
