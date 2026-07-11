from time import time

from loguru import logger

from piltover.utils.logging_loguru_handler import configure_logging

configure_logging()

logger.info("Importing piltover.tl...")

start_time = time()
import piltover.tl

logger.info(f"Importing tl piltover.tl module took {time() - start_time:.2f} seconds.")
del start_time