# core/responses.py
"""
Standard API response utilities for consistent error handling.
"""

from flask import jsonify
from functools import wraps


def success_response(data=None, message=None, status_code=200):
    """
    Create a standardized success response.
    
    Args:
        data: Response payload
        message: Success message
        status_code: HTTP status code (default 200)
    
    Returns:
        Tuple of (response, status_code)
    """
    response = {
        'success': True
    }
    if message:
        response['message'] = message
    if data is not None:
        response['data'] = data
    return jsonify(response), status_code


def error_response(message, error_code=None, details=None, status_code=400):
    """
    Create a standardized error response.
    
    Args:
        message: Error message for the user
        error_code: Application-specific error code (e.g., 'AUTH_001')
        details: Additional error details
        status_code: HTTP status code (default 400)
    
    Returns:
        Tuple of (response, status_code)
    """
    response = {
        'success': False,
        'message': message
    }
    if error_code:
        response['error_code'] = error_code
    if details:
        response['details'] = details
    return jsonify(response), status_code


# Common error responses as shortcuts
def not_found(message="Resource not found", error_code="NOT_FOUND"):
    return error_response(message, error_code=error_code, status_code=404)


def unauthorized(message="Unauthorized access", error_code="UNAUTHORIZED"):
    return error_response(message, error_code=error_code, status_code=401)


def forbidden(message="Access forbidden", error_code="FORBIDDEN"):
    return error_response(message, error_code=error_code, status_code=403)


def bad_request(message="Invalid request", error_code="BAD_REQUEST", details=None):
    return error_response(message, error_code=error_code, details=details, status_code=400)


def server_error(message="Internal server error", error_code="SERVER_ERROR"):
    return error_response(message, error_code=error_code, status_code=500)


def validation_error(errors, message="Validation failed"):
    """
    Return a validation error with field-specific errors.
    
    Args:
        errors: Dict of field -> error message
        message: General validation error message
    """
    return error_response(message, error_code="VALIDATION_ERROR", details=errors, status_code=400)


# Decorator for route exception handling
def handle_exceptions(f):
    """
    Decorator that catches exceptions and returns standardized error responses.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except ValueError as e:
            return bad_request(str(e), error_code="VALIDATION_ERROR")
        except Exception as e:
            import traceback
            traceback.print_exc()
            return server_error(f"An unexpected error occurred: {str(e)}")
    return decorated


# Error codes reference
ERROR_CODES = {
    # Authentication errors (AUTH_xxx)
    'AUTH_001': 'Email already exists',
    'AUTH_002': 'Invalid credentials',
    'AUTH_003': 'Token expired',
    'AUTH_004': 'Invalid token',
    'AUTH_005': 'Login required',
    
    # Resource errors (RES_xxx)
    'RES_001': 'Resource not found',
    'RES_002': 'Resource already exists',
    'RES_003': 'Resource access denied',
    
    # Validation errors (VAL_xxx)
    'VAL_001': 'Required field missing',
    'VAL_002': 'Invalid field format',
    'VAL_003': 'Field value out of range',
    
    # Business logic errors (BIZ_xxx)
    'BIZ_001': 'Operation not allowed',
    'BIZ_002': 'Maximum limit exceeded',
    'BIZ_003': 'Insufficient permissions',
    
    # Server errors (SRV_xxx)
    'SRV_001': 'Database error',
    'SRV_002': 'External service error',
    'SRV_003': 'Configuration error',
}
