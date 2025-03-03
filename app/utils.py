import os
import logging

from .settings import settings

logger = logging.getLogger("bg_removal_api")


def cleanup_temp_files(file_path: str) -> None:
    if not settings.KEEP_TEMP_FILES and os.path.exists(file_path):
        try:
            os.remove(file_path)
            logger.debug(f"Removed temporary file: {file_path}")
        except Exception as e:
            logger.error(
                f"Failed to remove temporary file {file_path}: {str(e)}")
