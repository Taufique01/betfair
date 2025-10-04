import time
from logger_factory import get_logger  # singleton logger

logger = get_logger()  # singleton logger

def safe_api_call(func, *args, retries=3, delay=2, **kwargs):
    """
    Safely call an API function with retries and exponential backoff.

    Args:
        func: Callable to execute.
        retries (int): Number of retries before giving up.
        delay (float): Initial delay in seconds between retries.
        *args, **kwargs: Arguments to pass to the callable.

    Returns:
        The result of func(*args, **kwargs), or None if all retries fail.
    """
    for attempt in range(1, retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"API call failed (attempt {attempt}/{retries}): {e}")
            if attempt < retries:
                time.sleep(delay)
                delay *= 2  # exponential backoff
            else:
                logger.exception("Max retries reached — giving up")
                return None
